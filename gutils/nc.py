#!python
# coding=utf-8
from __future__ import division

import os
import json
import math
import shutil
import argparse
import calendar
import tempfile
from glob import glob
from pathlib import Path
from datetime import datetime
from collections import OrderedDict

import pandas as pd
import netCDF4 as nc4
from compliance_checker.runner import ComplianceChecker, CheckSuite
from pocean.cf import cf_safe_name
from pocean.utils import (
    dict_update,
    get_fill_value,
    create_ncvar_from_series,
    get_ncdata_from_series
)
from pocean.meta import MetaInterface
from pocean.dsg import (
    IncompleteMultidimensionalTrajectory,
    ContiguousRaggedTrajectoryProfile
)

from gutils import get_uv_data, get_profile_data, safe_makedirs, setup_cli_logger
from gutils.filters import process_dataset
from gutils.slocum import SlocumReader

import logging
logging.getLogger("urllib3").setLevel(logging.WARNING)
L = logging.getLogger(__name__)


class ProfileIdTypes(object):
    """Types of profile IDs"""

    EPOCH = 1  # epochs
    COUNT = 2  # "count" from the output directory
    FRAME = 3  # "profile" column from the input dataframe


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


def set_scalar_value(value, ncvar):
    if value is None or math.isnan(value):
        ncvar[:] = get_fill_value(ncvar)
    else:
        ncvar[:] = value


def set_profile_data(ncd, profile_txy, profile_index):
    prof_t = ncd.variables['profile_time']
    prof_y = ncd.variables['profile_lat']
    prof_x = ncd.variables['profile_lon']
    prof_id = ncd.variables['profile_id']

    t_value = profile_txy.t
    if isinstance(t_value, datetime):
        t_value = nc4.date2num(
            t_value,
            units=prof_t.units,
            calendar=getattr(prof_t, 'calendar', 'standard')
        )
    set_scalar_value(t_value, prof_t)
    set_scalar_value(profile_txy.y, prof_y)
    set_scalar_value(profile_txy.x, prof_x)
    set_scalar_value(profile_index, prof_id)

    ncd.sync()


def set_uv_data(ncd, uv_txy):
    # The uv index should be the second row where v (originally m_water_vx) is not null
    uv_t = ncd.variables['time_uv']
    uv_x = ncd.variables['lon_uv']
    uv_y = ncd.variables['lat_uv']
    uv_u = ncd.variables['u']
    uv_v = ncd.variables['v']

    t_value = uv_txy.t
    if isinstance(t_value, datetime):
        t_value = nc4.date2num(
            t_value,
            units=uv_t.units,
            calendar=getattr(uv_t, 'calendar', 'standard')
        )
    set_scalar_value(t_value, uv_t)
    set_scalar_value(uv_txy.y, uv_y)
    set_scalar_value(uv_txy.x, uv_x)
    set_scalar_value(uv_txy.u, uv_u)
    set_scalar_value(uv_txy.v, uv_v)

    ncd.sync()


def set_extra_data(ncd, extras_df):
    """
    extras_df must have a single datetime index, all columns will be variables
    dimensioned by that index.
    """
    if extras_df.empty:
        return

    dims = ('extras',)
    extras_df = extras_df.reset_index()
    extras_df = extras_df.drop(columns=['profile'])

    ncd.createDimension(dims[0], len(extras_df))
    for c in extras_df.columns:
        var_name = cf_safe_name(c)

        v = create_ncvar_from_series(
            ncd,
            var_name,
            dims,
            extras_df[c],
            zlib=True,
            complevel=1
        )
        vvalues = get_ncdata_from_series(extras_df[c], v)
        v[:] = vvalues


def get_geographic_attributes(profile):
    miny = round(profile.y.min(), 5)
    maxy = round(profile.y.max(), 5)
    minx = round(profile.x.min(), 5)
    maxx = round(profile.x.max(), 5)
    polygon_wkt = 'POLYGON ((' \
        '{maxy:.6f} {minx:.6f}, '  \
        '{maxy:.6f} {maxx:.6f}, '  \
        '{miny:.6f} {maxx:.6f}, '  \
        '{miny:.6f} {minx:.6f}, '  \
        '{maxy:.6f} {minx:.6f}'    \
        '))'.format(
            miny=miny,
            maxy=maxy,
            minx=minx,
            maxx=maxx
        )
    return {
        'attributes': {
            'geospatial_lat_min': miny,
            'geospatial_lat_max': maxy,
            'geospatial_lon_min': minx,
            'geospatial_lon_max': maxx,
            'geospatial_bounds': polygon_wkt
        }
    }


def get_vertical_attributes(profile):
    return {
        'attributes': {
            'geospatial_vertical_min': round(profile.z.min(), 6),
            'geospatial_vertical_max': round(profile.z.max(), 6),
            'geospatial_vertical_units': 'm',
        }
    }


def get_temporal_attributes(profile):
    mint = profile.t.min()
    maxt = profile.t.max()
    return {
        'attributes': {
            'time_coverage_start': mint.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'time_coverage_end': maxt.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'time_coverage_duration': (maxt - mint).isoformat(),
        }
    }


def get_creation_attributes(profile):
    nc_create_ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    return {
        'attributes': {
            'date_created': nc_create_ts,
            'date_issued': nc_create_ts,
            'date_modified': nc_create_ts,
            'history': '{} - {}'.format(
                nc_create_ts,
                'Created with the GUTILS package: https://github.com/SECOORA/GUTILS'
            )
        }
    }


def create_profile_netcdf(attrs, profile, output_path, mode, profile_id_type=ProfileIdTypes.EPOCH,
                          extras_df=None):

    if extras_df is None:
        extras_df = pd.DataFrame()

    try:
        # Path to hold file while we create it
        tmp_handle, tmp_path = tempfile.mkstemp(suffix='.nc', prefix='gutils_glider_netcdf_')

        profile_time = profile.t.dropna().iloc[0]

        if profile_id_type == ProfileIdTypes.EPOCH:
            # We are using the epoch as the profile_index!
            profile_index = calendar.timegm(profile_time.utctimetuple())
        # Figure out which profile index to use (epoch or integer)
        elif profile_id_type == ProfileIdTypes.COUNT:
            # Get all existing netCDF outputs and find out the index of this netCDF file. That
            # will be the profile_id of this file. This is effectively keeping a tally of netCDF
            # files that have been created and only works if NETCDF FILES ARE WRITTEN IN
            # ASCENDING ORDER.
            # There is a race condition here if files are being in parallel and one should be
            # sure that when this function is being run there can be no more files written.
            # This file being written is the last profile available.
            netcdf_files_same_mode = list(glob(
                os.path.join(
                    output_path,
                    '*_{}.nc'.format(mode)
                )
            ))
            profile_index = len(netcdf_files_same_mode)
        elif profile_id_type == ProfileIdTypes.FRAME:
            profile_index = profile.profile.iloc[0]
        else:
            raise ValueError('{} is not a valid profile type'.format(profile_id_type))

        # Create final filename
        filename = "{0}_{1:010d}_{2:%Y%m%dT%H%M%S}Z_{3}.nc".format(
            attrs['glider'],
            profile_index,
            profile_time,
            mode
        )
        output_file = os.path.join(output_path, filename)

        # Add in the trajectory dimension to make pocean happy
        traj_name = '{}-{}'.format(
            attrs['glider'],
            attrs['trajectory_date']
        )
        profile = profile.assign(trajectory=traj_name)

        # We add this back in later
        profile.drop('profile', axis=1, inplace=True)

        # Compute U/V scalar values
        uv_txy = get_uv_data(profile)
        if 'u_orig' in profile.columns and 'v_orig' in profile.columns:
            profile.drop(['u_orig', 'v_orig'], axis=1, inplace=True)

        # Compute profile scalar values
        profile_txy = get_profile_data(profile, method=None)

        # Calculate some geographic global attributes
        attrs = dict_update(attrs, get_geographic_attributes(profile))
        # Calculate some vertical global attributes
        attrs = dict_update(attrs, get_vertical_attributes(profile))
        # Calculate some temporal global attributes
        attrs = dict_update(attrs, get_temporal_attributes(profile))
        # Set the creation dates and history
        attrs = dict_update(attrs, get_creation_attributes(profile))

        # Changing column names here from the default 't z x y'
        axes = {
            't': 'time',
            'z': 'depth',
            'x': 'lon',
            'y': 'lat',
            'sample': 'time'
        }
        profile = profile.rename(columns=axes)

        # Use pocean to create NetCDF file
        with IncompleteMultidimensionalTrajectory.from_dataframe(
                profile,
                tmp_path,
                axes=axes,
                reduce_dims=True,
                mode='a') as ncd:

            # Set an extras data
            set_extra_data(ncd, extras_df)

            # We only want to apply metadata from the `attrs` map if the variable is already in
            # the netCDF file or it is a scalar variable (no shape defined). This avoids
            # creating measured variables that were not measured in this profile.
            prof_attrs = attrs.copy()

            vars_to_update = OrderedDict()
            for vname, vobj in prof_attrs['variables'].items():
                if vname in ncd.variables or ('shape' not in vobj and 'type' in vobj):
                    if 'shape' in vobj:
                        # Assign coordinates
                        vobj['attributes']['coordinates'] = '{} {} {} {}'.format(
                            axes.get('t'),
                            axes.get('z'),
                            axes.get('x'),
                            axes.get('y'),
                        )
                    vars_to_update[vname] = vobj
                else:
                    # L.debug("Skipping missing variable: {}".format(vname))
                    pass

            prof_attrs['variables'] = vars_to_update
            ncd.apply_meta(prof_attrs)

            # Set trajectory value
            ncd.id = traj_name
            ncd.variables['trajectory'][0] = traj_name

            # Set profile_* data
            set_profile_data(ncd, profile_txy, profile_index)

            # Set *_uv data
            set_uv_data(ncd, uv_txy)

        # Move to final destination
        safe_makedirs(os.path.dirname(output_file))
        os.chmod(tmp_path, 0o664)
        shutil.move(tmp_path, output_file)
        L.info('Created: {}'.format(output_file))
        return output_file
    except BaseException:
        raise
    finally:
        os.close(tmp_handle)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def change_datatype(data, c, attrs):
    if c in attrs.get('variables', {}) and attrs['variables'][c].get('type'):
        try:
            ztype = attrs['variables'][c]['type']
            return data[c].astype(ztype)
        except ValueError:
            try:
                if '_FillValue' in attrs['variables'][c]:
                    if 'data' in attrs['variables'][c]['_FillValue']:
                        return data[c].fillna(attrs['variables'][c]['_FillValue']['data']).astype(ztype)
                    else:
                        return data[c].fillna(attrs['variables'][c]['_FillValue']).astype(ztype)
            except ValueError:
                L.error("Could not covert {} to {}. Skipping {}.".format(c, ztype, c))

    return None


def create_netcdf(attrs, data, output_path, mode, profile_id_type=ProfileIdTypes.EPOCH,
                  subset=True, extras_df=None):

    if extras_df is None:
        extras_df = pd.DataFrame()

    # Create NetCDF Files for Each Profile
    written_files = []

    for df in [data, extras_df]:

        # Optionally, remove any variables from the dataframe that do not have metadata assigned
        if subset is True:
            all_columns = set(df.columns)
            reserved_columns = [
                'trajectory',
                'profile',
                't',
                'x',
                'y',
                'z',
                'u_orig',
                'v_orig'
            ]
            removable_columns = all_columns - set(reserved_columns)
            orphans = removable_columns - set(attrs.get('variables', {}).keys())
            L.debug(
                "Excluded from output (absent from JSON config):\n  * {}".format('\n  * '.join(orphans))
            )
            df.drop(orphans, axis=1, inplace=True)

        # Change to the datatype defined in the JSON. This is so
        # all netCDF files have the same dtypes for the variables in the end
        for c in df.columns:
            changed = change_datatype(df, c, attrs)
            if changed is not None:
                df[c] = changed

    written = []
    for pi, profile in data.groupby('profile'):

        profile_extras = pd.DataFrame()
        if not extras_df.empty:
            profile_extras = extras_df.loc[extras_df.profile == pi]

        try:
            cr = create_profile_netcdf(attrs, profile, output_path, mode, profile_id_type,
                                       extras_df=profile_extras)
            written.append(cr)
        except BaseException:
            L.exception('Error creating netCDF for profile {}. Skipping.'.format(pi))
            continue

    return written_files


def create_arg_parser():
    parser = argparse.ArgumentParser(
        description='Parses a single combined ASCII file into a set of '
                    'NetCDFs file according to JSON configurations '
                    'for institution, deployment, glider, and datatypes.'
    )
    parser.add_argument(
        'file',
        help="Combined ASCII file to process into NetCDF"
    )
    parser.add_argument(
        'deployments_path',
        help='Path to folder containing all deployment config and for file output.'
    )
    parser.add_argument(
        "-r",
        "--reader_class",
        help="Glider reader to interpret the data",
        default='slocum'
    )
    parser.add_argument(
        '-ts', '--tsint',
        help="Interpolation window to consider when assigning profiles",
        default=None,
        type=int
    )
    parser.add_argument(
        '-fp', '--filter_points',
        help="Filter out profiles that do not have at least this number of points",
        default=None,
        type=int
    )
    parser.add_argument(
        '-fd', '--filter_distance',
        help="Filter out profiles that do not span at least this vertical distance (meters)",
        default=None,
        type=float
    )
    parser.add_argument(
        '-ft', '--filter_time',
        help="Filter out profiles that last less than this numer of seconds",
        default=None,
        type=float
    )
    parser.add_argument(
        '-fz', '--filter_z',
        help="Filter out profiles that are not completely below this depth (meters)",
        default=None,
        type=float
    )
    parser.add_argument(
        "-za",
        "--z_axis_method",
        help="1 == Calculate depth from pressure, 2 == Use raw depth values",
        default=1,
        type=int
    )
    parser.add_argument(
        '--no-subset',
        dest='subset',
        action='store_false',
        help='Process all variables - not just those available in a datatype mapping JSON file'
    )
    parser.add_argument(
        "-t",
        "--template",
        help="The template to use when writing netCDF files. Options: None, [filepath], trajectory, ioos_ngdac",
        default='trajectory'
    )
    parser.set_defaults(subset=True)

    return parser


def create_dataset(file,
    reader_class,
    deployments_path,
    subset,
    template,
    profile_id_type,
    prefer_file_filters=False,
    **filter_args
):
    # Remove None filters from the arguments
    filter_args = { k: v for k, v in filter_args.items() if v is not None }

    # Figure out the netCDF output path based on the file and the deployments_path
    dep_path = Path(deployments_path)
    file_path = Path(file)
    individual_dep_path = None
    for pp in file_path.parents:
        if dep_path == pp:
            break
        individual_dep_path = pp
    config_path = individual_dep_path / 'config'

    # Extract the filters from the config and override with passed in filters that are not None
    attrs = read_attrs(config_path, template=template)
    file_filters = attrs.pop('filters', {})

    # By default the filters passed in as filter_args will overwrite the filters defined in the
    # config file. If the opposite should happen (typically on a watch that uses a global set
    # of command line filters), you can set prefer_file_filters=True to have the file filters
    # take precedence over the passed in filters.
    if prefer_file_filters is False:
        filters = dict_update(file_filters, filter_args)
    else:
        filters = dict_update(filter_args, file_filters)

    processed_df, extras_df, mode = process_dataset(file, reader_class, **filters)

    if processed_df is None:
        return 1

    output_path = individual_dep_path / mode / 'netcdf'
    return create_netcdf(attrs, processed_df, output_path, mode, profile_id_type,
                         subset=subset, extras_df=extras_df)


def main_create():
    setup_cli_logger(logging.INFO)

    parser = create_arg_parser()
    args = parser.parse_args()

    filter_args = vars(args)
    # Remove non-filter args into positional arguments
    file = filter_args.pop('file')
    deployments_path = filter_args.pop('deployments_path')
    subset = filter_args.pop('subset')
    template = filter_args.pop('template')

    # Move reader_class to a class
    reader_class = filter_args.pop('reader_class')
    if reader_class == 'slocum':
        reader_class = SlocumReader

    return create_dataset(
        file=file,
        reader_class=reader_class,
        deployments_path=deployments_path,
        subset=subset,
        template=template,
        **filter_args
    )


# CHECKER
def check_dataset(args):
    check_suite = CheckSuite()
    check_suite.load_all_available_checkers()

    outhandle, outfile = tempfile.mkstemp()

    def show_messages(jn, log):
        out_messages = []
        for k, v in jn.items():
            if isinstance(v, list):
                for x in v:
                    if 'msgs' in x and x['msgs']:
                        out_messages += x['msgs']
        log(
            '{}:\n{}'.format(args.file, '\n'.join(['  * {}'.format(
                m) for m in out_messages ])
            )
        )

    try:
        return_value, errors = ComplianceChecker.run_checker(
            ds_loc=args.file,
            checker_names=['gliderdac:3.0'],
            verbose=2,
            criteria='lenient',
            skip_checks=[
                # This takes forever and hurts my CPU. Skip it.
                'check_standard_names:A',
            ],
            output_format='json',
            output_filename=outfile
        )
    except BaseException as e:
        L.warning('{} - {}'.format(args.file, e))
        return 1
    else:
        if errors is False:
            return_value = 0
            log = L.debug
        else:
            return_value = 1
            log = L.warning

        with open(outfile, 'rt') as f:
            show_messages(json.loads(f.read())['gliderdac:3.0'], log)

        return return_value
    finally:
        os.close(outhandle)
        if os.path.isfile(outfile):
            os.remove(outfile)


def check_arg_parser():
    parser = argparse.ArgumentParser(
        description='Verifies that a glider NetCDF file from a provider '
                    'contains all the required global attributes, dimensions,'
                    'scalar variables and dimensioned variables.'
    )

    parser.add_argument(
        'file',
        help='Path to Glider NetCDF file.'
    )
    return parser


def main_check():
    setup_cli_logger(logging.INFO)

    parser = check_arg_parser()
    args = parser.parse_args()

    # Check filenames
    if args.file is None:
        raise ValueError('Must specify path to NetCDF file')

    return check_dataset(args)


def merge_profile_netcdf_files(folder, output):
    import pandas as pd
    from glob import glob

    new_fp, new_path = tempfile.mkstemp(suffix='.nc', prefix='gutils_merge_')

    try:
        # Get the number of profiles
        members = sorted(list(glob(os.path.join(folder, '*.nc'))))

        # Iterate over the netCDF files and create a dataframe for each
        dfs = []
        axes = {
            'trajectory': 'trajectory',
            't': 'time',
            'x': 'lon',
            'y': 'lat',
            'z': 'depth',
        }
        for ncf in members:
            with IncompleteMultidimensionalTrajectory(ncf) as old:
                df = old.to_dataframe(axes=axes, clean_cols=False)
                dfs.append(df)

        full_df = pd.concat(dfs, ignore_index=True, sort=False)
        full_df = full_df.sort_values(['trajectory', 'profile_id', 'profile_time', 'depth'])

        # Now add a profile axes
        axes = {
            'trajectory': 'trajectory',
            'profile': 'profile_id',
            't': 'profile_time',
            'x': 'profile_lon',
            'y': 'profile_lat',
            'z': 'depth',
        }

        newds = ContiguousRaggedTrajectoryProfile.from_dataframe(
            full_df,
            output=new_path,
            axes=axes,
            mode='a'
        )

        # Apply default metadata
        attrs = read_attrs(template='ioos_ngdac')
        newds.apply_meta(attrs, create_vars=False, create_dims=False)
        newds.close()

        safe_makedirs(os.path.dirname(output))
        shutil.move(new_path, output)
    finally:
        os.close(new_fp)
        if os.path.exists(new_path):
            os.remove(new_path)


def process_folder(deployment_path, mode, merger_class, reader_class, subset=True, template='trajectory', profile_id_type=ProfileIdTypes.EPOCH, workers=4, **filters):

    from multiprocessing import Pool

    binary_path = os.path.join(deployment_path, mode, 'binary')
    ascii_path = os.path.join(deployment_path, mode, 'ascii')

    # Make ASCII files
    merger = merger_class(
        binary_path,
        ascii_path
    )
    # The merge results contain a reference to the new produced ASCII file as well as what binary files went into it.
    merger.convert()

    asciis = sorted(
        [ x.path for x in os.scandir(ascii_path) if Path(x).suffix in ['.dat']]
    )

    with Pool(processes=workers) as pool:
        kwargs = dict(
            reader_class=SlocumReader,
            deployments_path=Path(str(deployment_path)).parent,
            subset=subset,
            template=template,
            profile_id_type=profile_id_type,
            **filters
        )

        multiple_results = [
            pool.apply_async(
                create_dataset, (), dict(file=x, **kwargs)
            ) for x in asciis
        ]

        print([ res.get() for res in multiple_results ])
