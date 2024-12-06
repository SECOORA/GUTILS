#!python
# coding=utf-8
from __future__ import division  # always return floats when dividing

import os
import math
import errno
import warnings
import subprocess
from io import StringIO
from collections import namedtuple

import numpy as np
import pandas as pd
from scipy.signal import boxcar, convolve

from pocean.meta import MetaInterface
from pocean.utils import (
    dict_update
)

import logging
L = logging.getLogger(__name__)

__version__ = "3.2.0"


def boxcar_smooth_dataset(dataset, window_size):
    window = boxcar(window_size)
    return convolve(dataset, window, 'same') / window_size


def validate_glider_args(*args):
    """Validates a glider dataset

    Performs the following changes and checks:
    * Makes sure that there are at least 2 points in the dataset
    * Checks for netCDF4 fill types and changes them to NaNs
    * Tests for finite values in time and depth arrays
    """

    arg_length = len(args[0])

    # Time is assumed to be the first dataset
    if arg_length < 2:
        raise IndexError('The time series must have at least two values')

    # Skipping first (time) argument
    for arg in args[1:]:
        # Make sure all arguments have the same length
        if len(arg) != arg_length:
            raise ValueError('Arguments must all be the same length')

        # Test for finite values
        if len(arg[np.isfinite(arg)]) == 0:
            raise ValueError('Data array has no finite values')


def get_decimal_degrees(lat_lon):
    """Converts NMEA GPS format (DDDmm.mmmm) to decimal degrees (DDD.dddddd)

    Parameters
    ----------
    lat_lon : str
        NMEA GPS coordinate (DDDmm.mmmm)

    Returns
    -------
    float
        Decimal degree coordinate (DDD.dddddd) or math.nan
    """

    # Absolute value of the coordinate
    try:
        pos_lat_lon = abs(lat_lon)
    except (TypeError, ValueError):
        return math.nan

    if math.isnan(pos_lat_lon):
        return lat_lon

    # Calculate NMEA degrees as an integer
    nmea_degrees = int(pos_lat_lon // 100) * 100

    # Subtract the NMEA degrees from the absolute value of lat_lon and divide by 60
    # to get the minutes in decimal format
    gps_decimal_minutes = (pos_lat_lon - nmea_degrees) / 60

    # Divide NMEA degrees by 100 and add the decimal minutes
    decimal_degrees = (nmea_degrees // 100) + gps_decimal_minutes

    # Round to 6 decimal places
    decimal_degrees = round(decimal_degrees, 6)

    if lat_lon < 0:
        return -decimal_degrees

    return decimal_degrees


def masked_epoch(timeseries):
    tmask = pd.isnull(timeseries)
    epochs = np.ma.MaskedArray(timeseries.astype(np.int64) // 1e9)
    epochs.mask = tmask
    return pd.Series(epochs)


def interpolate_gps(timestamps, latitude, longitude):
    """Calculates interpolated GPS coordinates between the two surfacings

    Parameters:
        'dataset': An N by 3 numpy array of time, lat, lon pairs
    Returns interpolated gps dataset over entire time domain of dataset
    """

    validate_glider_args(timestamps, latitude, longitude)

    est_lat = np.array([np.nan] * latitude.size)
    est_lon = np.array([np.nan] * longitude.size)

    anynull = (timestamps.isnull()) | (latitude.isnull()) | (longitude.isnull())
    newtimes = timestamps.loc[~anynull]
    latitude = latitude.loc[~anynull]
    longitude = longitude.loc[~anynull]

    if latitude.size == 0 or longitude.size == 0:
        L.warning('GPS time-seies contains no valid GPS fixes for interpolation')
        return est_lat, est_lon

    # If only one GPS point, make it the same for the entire dataset
    if latitude.size == 1 and longitude.size == 1:
        est_lat[:] = latitude.iloc[0]
        est_lon[:] = longitude.iloc[0]
    else:
        # Interpolate data
        est_lat = np.interp(
            timestamps,
            newtimes,
            latitude,
            left=latitude.iloc[0],
            right=latitude.iloc[-1]
        )
        est_lon = np.interp(
            timestamps,
            newtimes,
            longitude,
            left=longitude.iloc[0],
            right=longitude.iloc[-1]
        )

    return est_lat, est_lon


def generate_stream(processArgs):
    """ Runs a given process and outputs the resulting text as a StringIO

    Parameters
    ----------
    processArgs : list
        Arguments to run in a process

    Returns
    -------
    StringIO
        Resulting text
    """
    process = subprocess.Popen(
        processArgs,
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = process.communicate()

    if stderr:
        L.error(stderr)

    return StringIO(stdout), process.returncode


def get_uv_data(profile):
    # Find and return t, x and y from the second row where U and V are not null
    t = np.nan
    x = np.nan
    y = np.nan
    u = np.nan
    v = np.nan

    if 'u_orig' in profile.columns and 'v_orig' in profile.columns:
        uvslice = (~profile.u_orig.isnull()) & (~profile.v_orig.isnull())
        uv_index = profile[uvslice].index[:2]
        if uv_index.size != 0:
            uv_index = uv_index[-1]
            with warnings.catch_warnings():
                # We don't care about dropping nanoseconds
                warnings.simplefilter("ignore")
                t = profile.t.loc[uv_index].to_pydatetime()
            x = profile.x.loc[uv_index]
            y = profile.y.loc[uv_index]
            u = profile.u_orig.loc[uv_index]
            v = profile.v_orig.loc[uv_index]

    tuv = namedtuple('UV_Data', ['t', 'x', 'y', 'u', 'v'])
    return tuv(t=t, x=x, y=y, u=u, v=v)


PROFILE_MEAN = 0
PROFILE_MEDIAN = 1
PROFILE_MINIMUM = 2


def get_profile_data(profile, method=None):
    # Find and return profile t, x and y from the data based on the method
    if method is None:
        method = PROFILE_MEAN

    t = np.nan
    x = np.nan
    y = np.nan

    if method == PROFILE_MEDIAN:
        # T,X,Y: MIDDLE INDEX (median)
        amedian = np.nanmedian(profile.y.values)
        middle_index = np.nanargmin(np.abs(profile.y.values - amedian))
        t = profile.t.iloc[middle_index].to_datetime()
        x = profile.x.iloc[middle_index]
        y = profile.y.iloc[middle_index]

    elif method == PROFILE_MEAN:
        # T,X,Y: AVERAGE
        all_t = (profile.t - pd.Timestamp('1970-01-01')) // pd.Timedelta('1ms')  # ms since epoch
        with warnings.catch_warnings():
            # We don't care about dropping nanoseconds
            warnings.simplefilter("ignore")
            t = pd.to_datetime(all_t.mean(), unit='ms').to_pydatetime()
        y = profile.y.mean()
        x = profile.x.mean()

    elif method == PROFILE_MINIMUM:
        # T: MIN
        # X,Y: AVERAGE
        t = profile.t.min().to_pydatetime()
        y = profile.y.mean()
        x = profile.x.mean()

    tuv = namedtuple('Profile_Data', ['t', 'x', 'y'])
    return tuv(t=t, x=x, y=y)


def read_attrs(config_path=None, template=None):

    def cfg_file(name):
        return os.path.join(
            config_path,
            name
        )

    template = template or 'trajectory'

    if os.path.isfile(template):
        default_attrs_path = template
    else:
        template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        default_attrs_path = os.path.join(template_dir, '{}.json'.format(template))
        if not os.path.isfile(default_attrs_path):
            L.error("Template path {} not found, using defaults.".format(default_attrs_path))
            default_attrs_path = os.path.join(template_dir, 'trajectory.json')

    # Load in template defaults
    defaults = dict(MetaInterface.from_jsonfile(default_attrs_path))

    # Load instruments
    ins = {}
    if config_path:
        ins_attrs_path = cfg_file("instruments.json")
        if os.path.isfile(ins_attrs_path):
            ins = dict(MetaInterface.from_jsonfile(ins_attrs_path))

    # Load deployment attributes (including some global attributes)
    deps = {}
    if config_path:
        deps_attrs_path = cfg_file("deployment.json")
        if os.path.isfile(deps_attrs_path):
            deps = dict(MetaInterface.from_jsonfile(deps_attrs_path))

    # Update, highest precedence updates last
    one = dict_update(defaults, ins)
    two = dict_update(one, deps)
    return two


def safe_makedirs(folder):
    try:
        os.makedirs(folder)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def setup_cli_logger(level=None):
    if level is None:
        level = logging.INFO

    sh = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    sh.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [sh]
