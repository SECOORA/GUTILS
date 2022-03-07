#!/usr/bin/env python
from lib2to3.pytree import Base
import os
import sys
import shutil
from glob import glob
from pathlib import Path
from tempfile import mkdtemp
from collections import OrderedDict

import numpy as np
import pandas as pd
from gsw import z_from_p, p_from_z

from gutils import (
    generate_stream,
    get_decimal_degrees,
    interpolate_gps,
    masked_epoch,
    safe_makedirs
)
from gutils.ctd import calculate_practical_salinity, calculate_density

import logging
L = logging.getLogger(__name__)


MODE_MAPPING = {
    "rt": ["sbd", "tbd", "mbd", "nbd"],
    "delayed": ["dbd", "ebd"]
}
ALL_EXTENSIONS = [".sbd", ".tbd", ".mbd", ".nbd", ".dbd", ".ebd"]


COMPUTE_PRESSURE = 1
USE_RAW_PRESSURE = 2

PSEUDOGRAM_DEPLOYMENTS = [
    'unit_507'
]


class SlocumReader(object):

    TIMESTAMP_SENSORS = ['m_present_time', 'sci_m_present_time']
    PRESSURE_SENSORS = ['sci_water_pressure', 'sci_water_pressure2', 'm_water_pressure', 'm_pressure']
    DEPTH_SENSORS = ['m_depth', 'm_water_depth']
    TEMPERATURE_SENSORS = ['sci_water_temp', 'sci_water_temp2']
    CONDUCTIVITY_SENSORS = ['sci_water_cond', 'sci_water_cond2']

    def __init__(self, ascii_file):
        self.ascii_file = ascii_file
        self.metadata, self.data = self.read()

        # Do we have a pseudogram file as well?
        self._extras = pd.DataFrame()

        # Set the mode to 'rt' or 'delayed'
        self.mode = None
        if 'filename_extension' in self.metadata:
            filemode = self.metadata['filename_extension']
            for m, extensions in MODE_MAPPING.items():
                if filemode in extensions:
                    self.mode = m
                    break

    def extras(self, data):
        """
        Process pseudogram and echometrics data here for now.  If the echometrics
        data is found, the echometrics (echodroid) variables are reassigned the
        extras dimension.
        """
        pse_file = Path(self.ascii_file).with_suffix(".pseudogram")
        if pse_file.exists():
            try:
                self._extras = pd.read_csv(
                    pse_file,
                    header=0,
                    names=[
                        'pseudogram_time',
                        'pseudogram_depth',
                        'pseudogram_sv'
                    ]
                )

                # Loop through echometrics values and assign them into available pseudogram times
                if 'sci_echodroid_sv' in data.columns:
                    # Only move echometrics data that is not NaN.  There is a curious
                    # problem from the science computer where the first row of echometrics
                    # data could be all zeroes.  Here we take valid Sv values < 0 dB.
                    echometricsData = data[data['sci_echodroid_sv'] < 0]
                    if len(echometricsData) > 0:
                        # Create echometrics variables in self._extras
                        ECHOMETRICS_SENSORS = ['sci_echodroid_aggindex', 'sci_echodroid_ctrmass',
                            'sci_echodroid_eqarea', 'sci_echodroid_inertia', 'sci_echodroid_propocc',
                            'sci_echodroid_sa', 'sci_echodroid_sv']
                        for sensor in ECHOMETRICS_SENSORS:
                            self._extras[sensor] = np.full((len(self._extras['pseudogram_time'])), np.nan)
                        for idx, row in echometricsData.iterrows():
                            tdiff = np.abs(np.unique(self._extras['pseudogram_time']) - row['m_present_time']).min()
                            # if a close enough match is found, assign the echometrics
                            # values into self._extras
                            if tdiff < 1e-6:
                                tidx = np.abs(np.unique(self._extras['pseudogram_time']) - row['m_present_time']).argmin()
                                ptime = np.unique(self._extras['pseudogram_time'])[tidx]
                                # Place values into the first time index that matches
                                ptimeidx = self._extras['pseudogram_time'][self._extras['pseudogram_time'] == ptime].axes[0].values[0]
                                # Update self._extras from the row of echometrics data
                                dataRow = row.get(ECHOMETRICS_SENSORS)
                                self._extras.update(pd.DataFrame(dataRow.to_dict(),index=[ptimeidx]))
                        # Once data is copied out of data, the columns have to be removed from data
                        # or the write to netCDF will fail.
                        data = data.drop(columns=ECHOMETRICS_SENSORS)

                self._extras['pseudogram_time'] = pd.to_datetime(
                    self._extras.pseudogram_time, unit='s', origin='unix'
                )
                self._extras = self._extras.sort_values([
                    'pseudogram_time',
                    'pseudogram_depth'
                ])
                self._extras.set_index("pseudogram_time", inplace=True)
            except BaseException as e:
                self._extras = pd.DataFrame()
                L.warning(f"Could not process pseudogram file {pse_file}: {e}")

        return self._extras, data

    def read(self):
        metadata = OrderedDict()
        headers = None
        with open(self.ascii_file, 'rt') as af:
            for li, al in enumerate(af):
                if 'm_present_time' in al:
                    headers = al.strip().split(' ')
                elif headers is not None:
                    data_start = li + 2  # Skip units line and the interger row after that
                    break
                else:
                    title, value = al.split(':', 1)
                    metadata[title.strip()] = value.strip()

        # Pull out the number of bytes for each column
        #   The last numerical field is the number of bytes transmitted for each sensor:
        #     1    A 1 byte integer value [-128 .. 127].
        #     2    A 2 byte integer value [-32768 .. 32767].
        #     4    A 4 byte float value (floating point, 6-7 significant digits,
        #                                approximately 10^-38 to 10^38 dynamic range).
        #     8    An 8 byte double value (floating point, 15-16 significant digits,
        #                                  approximately 10^-308 to 10^308 dyn. range).
        dtypedf = pd.read_csv(
            self.ascii_file,
            index_col=False,
            skiprows=data_start - 1,
            nrows=1,
            header=None,
            names=headers,
            sep=' ',
            skip_blank_lines=True,
        )

        def intflag_to_dtype(intvalue):
            if intvalue == 1:
                return np.object  # ints can't have NaN so use object for now
            elif intvalue == 2:
                return np.object  # ints can't have NaN so use object for now
            elif intvalue == 4:
                return np.float32
            elif intvalue == 8:
                return np.float64
            else:
                return np.object

        inttypes = [ intflag_to_dtype(x) for x in dtypedf.iloc[0].astype(int).values ]
        dtypes = dict(zip(dtypedf.columns, inttypes))

        df = pd.read_csv(
            self.ascii_file,
            index_col=False,
            skiprows=data_start,
            header=None,
            names=headers,
            dtype=dtypes,
            sep=' ',
            skip_blank_lines=True,
        )
        return metadata, df

    def standardize(self, gps_prefix=None, z_axis_method=COMPUTE_PRESSURE):

        df = self.data.copy()

        # Convert NMEA coordinates to decimal degrees
        for col in df.columns:
            # Ignore if the m_gps_lat and/or m_gps_lon value is the default masterdata value
            if col.endswith('_lat'):
                df[col] = df[col].map(lambda x: get_decimal_degrees(x) if x <= 9000 else np.nan)
            elif col.endswith('_lon'):
                df[col] = df[col].map(lambda x: get_decimal_degrees(x) if x < 18000 else np.nan)

        # Standardize 'time' to the 't' column
        for t in self.TIMESTAMP_SENSORS:
            if t in df.columns:
                df['t'] = pd.to_datetime(df[t], unit='s')
                break

        # Interpolate GPS coordinates
        if 'm_gps_lat' in df.columns and 'm_gps_lon' in df.columns:

            df['drv_m_gps_lat'] = df.m_gps_lat.copy()
            df['drv_m_gps_lon'] = df.m_gps_lon.copy()

            # Fill in data will nulls where value is the default masterdata value
            masterdatas = (df.drv_m_gps_lon >= 18000) | (df.drv_m_gps_lat > 9000)
            df.loc[masterdatas, 'drv_m_gps_lat'] = np.nan
            df.loc[masterdatas, 'drv_m_gps_lon'] = np.nan

            try:
                # Interpolate the filled in 'x' and 'y'
                y_interp, x_interp = interpolate_gps(
                    masked_epoch(df.t),
                    df.drv_m_gps_lat,
                    df.drv_m_gps_lon
                )
            except (ValueError, IndexError):
                L.warning("Raw GPS values not found!")
                y_interp = np.empty(df.drv_m_gps_lat.size) * np.nan
                x_interp = np.empty(df.drv_m_gps_lon.size) * np.nan

            df['y'] = y_interp
            df['x'] = x_interp

        if z_axis_method == COMPUTE_PRESSURE:
            """
            ---- Option 1: Always calculate Z from pressure ----
            It's really a matter of data provider preference and varies from one provider to another.
            That being said, typically the sci_water_pressure or m_water_pressure variables, if present
            in the raw data files, will typically have more non-NaN values than m_depth.  For example,
            all MARACOOS gliders typically have both m_depth and sci_water_pressure contained in them.
            However, m_depth is typically heavily decimated while sci_water_pressure contains a more
            complete pressure record.  So, while we transmit both m_depth and sci_water_pressure, I
            calculate depth from pressure & (interpolated) latitude and use that as my NetCDF depth
            variable. - Kerfoot
            """
            # Search for a 'pressure' column
            for p in self.PRESSURE_SENSORS:
                if p in df.columns:
                    # Convert bar to dbar here
                    df['pressure'] = df[p].copy() * 10
                    # Calculate depth from pressure and latitude
                    # Negate the results so that increasing values note increasing depths
                    df['z'] = -z_from_p(df.pressure, df.y)
                    break

            if 'z' not in df and 'pressure' not in df:
                # Search for a 'z' column
                for p in self.DEPTH_SENSORS:
                    if p in df.columns:
                        df['z'] = df[p].copy()
                        # Calculate pressure from depth and latitude
                        # Negate the results so that increasing values note increasing depth
                        df['pressure'] = -p_from_z(df.z, df.y)
                        break

        elif z_axis_method == USE_RAW_PRESSURE:
            """
            ---- Option 2: Use raw pressure/depth data that was sent across ----
            """
            # Standardize to the 'pressure' column
            for p in self.PRESSURE_SENSORS:
                if p in df.columns:
                    # Convert bar to dbar here
                    df['pressure'] = df[p].copy() * 10
                    break

            # Standardize to the 'z' column
            for p in self.DEPTH_SENSORS:
                if p in df.columns:
                    df['z'] = df[p].copy()
                    break

            # Don't calculate Z from pressure if a metered depth column exists already
            if 'pressure' in df and 'z' not in df:
                # Calculate depth from pressure and latitude
                # Negate the results so that increasing values note increasing depths
                df['z'] = -z_from_p(df.pressure, df.y)

            if 'z' in df and 'pressure' not in df:
                # Calculate pressure from depth and latitude
                # Negate the results so that increasing values note increasing depth
                df['pressure'] = -p_from_z(df.z, df.y)

        else:
            raise ValueError("No z-axis method exists for {}".format(z_axis_method))

        rename_columns = {
            'm_water_vx': 'u_orig',
            'm_water_vy': 'v_orig',
        }

        # These need to be standardize so we can compute salinity and density!
        for vname in self.TEMPERATURE_SENSORS:
            if vname in df.columns:
                rename_columns[vname] = 'temperature'
                break
        for vname in self.CONDUCTIVITY_SENSORS:
            if vname in df.columns:
                rename_columns[vname] = 'conductivity'
                break

        # Standardize columns
        df = df.rename(columns=rename_columns)

        # Compute additional columns
        df = self.compute(df)

        return df

    def compute(self, df):
        try:
            # Compute salinity
            df['salinity'] = calculate_practical_salinity(
                conductivity=df.conductivity.values,
                temperature=df.temperature.values,
                pressure=df.pressure.values,
            )
        except (ValueError, AttributeError) as e:
            L.error("Could not compute salinity for {}: {}".format(self.ascii_file, e))

        try:
            # Compute density
            df['density'] = calculate_density(
                temperature=df.temperature.values,
                pressure=df.pressure.values,
                salinity=df.salinity.values,
                latitude=df.y,
                longitude=df.x,
            )
        except (ValueError, AttributeError) as e:
            L.error("Could not compute density for {}: {}".format(self.ascii_file, e))

        return df


class SlocumMerger(object):
    """
    Merges flight and science data files into an ASCII file.

    Copies files matching the regex in source_directory to their own temporary directory
    before processing since the Rutgers supported script only takes foldesr as input

    Returns a list of flight/science files that were processed into ASCII files
    """

    def __init__(self, source_directory, destination_directory, cache_directory=None, globs=None):

        globs = globs or ['*']

        self.tmpdir = mkdtemp(prefix='gutils_convert_')
        self.matched_files = []
        self.cache_directory = cache_directory or source_directory
        self.destination_directory = destination_directory
        self.source_directory = source_directory

        mf = set()
        for g in globs:
            mf.update(
                glob(
                    os.path.join(
                        source_directory,
                        g
                    )
                )
            )

        def slocum_binary_sorter(x):
            """ Sort slocum binary files correctly, using leading zeros.leading """
            'usf-bass-2014-048-2-1.tbd -> 2014_048_00000002_000000001'
            x, ext = os.path.splitext(os.path.basename(x))
            if ext not in ALL_EXTENSIONS:
                return x
            z = [ int(a) for a in x.split('-')[-4:] ]
            return '{0[0]:04d}_{0[1]:03d}_{0[2]:08d}_{0[3]:08d}'.format(z)

        self.matched_files = sorted(list(mf), key=slocum_binary_sorter)

    def __del__(self):
        # Remove tmpdir
        try:
            shutil.rmtree(self.tmpdir)
        except FileNotFoundError:
            pass

    def convert(self):
        # Copy to tempdir
        for f in self.matched_files:
            fname = os.path.basename(f)
            tmpf = os.path.join(self.tmpdir, fname)
            shutil.copy2(f, tmpf)

        safe_makedirs(self.destination_directory)

        # Run conversion script
        convert_binary_path = os.path.join(
            os.path.dirname(__file__),
            'bin',
            'convertDbds.sh'
        )
        pargs = [
            convert_binary_path,
            '-q',
            '-p',
            '-c', self.cache_directory
        ]

        # Perform pseudograms if this ASCII file matches the deployment
        # name of things we know to have the data. There needs to be a
        # better way to figure this out, but we don't have any understanding
        # of a deployment config object at this point.

        # Ideally this code isn't tacked into convertDbds.sh to output separate
        # files and can be done using the ASCII files exported from SlocumMerger
        # using pandas. Tighter integration into GUTILS will be done by
        # Rob Cermak@{UAF,UW}/John Horne@UW.  For now, the code is separate
        # and placed in # GUTILS/gutils/slocum/echotools.
        # The tools require one additional python library: dbdreader
        # https://github.com/smerckel/dbdreader
        for d in PSEUDOGRAM_DEPLOYMENTS:
            if d in self.matched_files[0]:
                pargs = pargs + [
                    '-y', sys.executable,
                    '-g',  # Makes the pseudogram ASCII
                    '-i',  # Makes the pseudogram images. This is slow!
                    '-r', "60.0"
                ]

        pargs.append(self.tmpdir)
        pargs.append(self.destination_directory)

        command_output, return_code = generate_stream(pargs)

        # Return
        processed = []
        output_files = command_output.read().split('\n')
        # iterate and every time we hit a .dat file we return the cache
        binary_files = []
        for x in output_files:

            if x.startswith('Error'):
                L.error(x)
                continue

            if x.startswith('Skipping'):
                continue

            fname = os.path.basename(x)
            _, suff = os.path.splitext(fname)

            if suff == '.dat':
                ascii_file = os.path.join(self.destination_directory, fname)
                if os.path.isfile(ascii_file):
                    processed.append({
                        'ascii': ascii_file,
                        'binary': sorted(binary_files)
                    })
                    L.info("Converted {} to {}".format(
                        ','.join([ os.path.basename(x) for x in sorted(binary_files) ]),
                        fname
                    ))
                else:
                    L.warning("{} not an output file".format(x))

                binary_files = []
            else:
                bf = os.path.join(self.source_directory, fname)
                if os.path.isfile(x):
                    binary_files.append(bf)

        return processed
