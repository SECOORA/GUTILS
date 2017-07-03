#!/usr/bin/env python
from __future__ import division  # always return floats when dividing

import os
import re
import math
import subprocess

from six import StringIO

import numpy as np
from scipy.signal import boxcar, convolve


def clean_dataset(dataset):
    # Get rid of NaNs
    dataset = dataset[~np.isnan(dataset[:, 1:]).any(axis=1), :]

    return dataset


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

    for arg in args:
        # Make sure all arguments have the same length
        if len(arg) != arg_length:
            raise ValueError('Arguments must all be the same length')

        # Set NC_FILL_VALUES to NaN for consistency if NetCDF lib available
        try:
            from netCDF4 import default_fillvals as NC_FILL_VALUES
            arg[arg == NC_FILL_VALUES['f8']] = float('nan')  # NOQA
        except ImportError:
            pass

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


def parse_glider_filename(filename):
    """
    Parses a glider filename and returns details in a dictionary

    Parameters
    ----------
    filename : str
        A filename to parse

    Returns
    -------
    dict
        Returns dictionary with the following keys:

            * 'glider': glider name
            * 'year': data file year created
            * 'day': data file julian date created
            * 'mission': data file mission id
            * 'segment': data file segment id
            * 'type': data file type
    """
    head, tail = os.path.split(filename)

    matches = re.search(r"([\w\d\-]+)-(\d+)-(\d+)-(\d+)-(\d+)\.(\w+)$", tail)

    if matches is not None:
        return {
            'path': head,
            'glider': matches.group(1),
            'year': int(matches.group(2)),
            'day': int(matches.group(3)),
            'mission': int(matches.group(4)),
            'segment': int(matches.group(5)),
            'type': matches.group(6)
        }
    else:
        raise ValueError(
            "Filename ({}) not in usual glider format: "
            "<glider name>-<year>-<julian day>-"
            "<mission>-<segment>.<extenstion>".format(filename)
        )


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
        stderr=subprocess.STDOUT
    )
    stdout, _ = process.communicate()
    return StringIO(stdout), process.returncode
