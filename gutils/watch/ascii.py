#!python
# coding=utf-8
import os
import sys
import argparse

from pyinotify import (
    IN_CLOSE_WRITE,
    IN_MOVED_TO,
    Notifier,
    NotifierError,
    ProcessEvent,
    WatchManager
)

from gutils import setup_cli_logger
from gutils.nc import create_dataset
from gutils.slocum import SlocumReader

import logging
L = logging.getLogger(__name__)


class Ascii2NetcdfProcessor(ProcessEvent):

    def my_init(
            self, deployments_path, subset, template, profile_id_type,
            prefer_file_filters=True, **filters
        ):
        self.deployments_path = deployments_path
        self.subset = subset
        self.template = template
        self.profile_id_type = profile_id_type
        self.prefer_file_filters = prefer_file_filters
        self.filters = filters

    def process_IN_CLOSE(self, event):
        self.convert_to_netcdf(event)

    def process_IN_MOVED_TO(self, event):
        self.convert_to_netcdf(event)


class Slocum2NetcdfProcessor(Ascii2NetcdfProcessor):

    VALID_EXTENSIONS = ['.dat']

    def __call__(self, event):
        if os.path.splitext(event.name)[-1] in self.VALID_EXTENSIONS:
            super().__call__(event)

    def convert_to_netcdf(self, event):
        create_dataset(
            file=event.pathname,
            reader_class=SlocumReader,
            deployments_path=self.deployments_path,
            subset=self.subset,
            template=self.template,
            profile_id_type=self.profile_id_type,
            prefer_file_filters=self.prefer_file_filters,
            **self.filters
        )


def create_netcdf_arg_parser():

    parser = argparse.ArgumentParser(
        description="Monitor a directory for new ASCII glider data and outputs NetCDF."
    )
    parser.add_argument(
        "-d",
        "--deployments_path",
        help="Path to deployments directory",
        default=os.environ.get('GUTILS_DEPLOYMENTS_DIRECTORY')
    )
    parser.add_argument(
        '-r',
        '--reader_class',
        help='Glider reader to interpret the data',
        default='slocum'
    )
    parser.add_argument(
        '-fp', '--filter_points',
        help="Filter out profiles that do not have at least this number of points",
        default=5,
        type=int
    )
    parser.add_argument(
        '-fd', '--filter_distance',
        help="Filter out profiles that do not span at least this vertical distance (meters)",
        default=1,
        type=float
    )
    parser.add_argument(
        '-ft', '--filter_time',
        help="Filter out profiles that last less than this number of seconds",
        default=10,
        type=float
    )
    parser.add_argument(
        '-fz', '--filter_z',
        help="Filter out profiles that are not completely below this depth (meters)",
        default=1,
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
        help="The template to use when writing netCDF files. Options: [filepath], trajectory, ioos_ngdac",
        default=os.environ.get('GUTILS_NETCDF_TEMPLATE', 'trajectory')
    )
    parser.add_argument(
        "-p",
        "--profile_id_type",
        help="The profile type to use when writing netCDF files. 1 == EPOCH, 2 == COUNT, 3 == FRAME",
        default=os.environ.get('GUTILS_PROFILE_ID_TYPE', 1),
        type=int
    )
    parser.add_argument(
        "--daemonize",
        help="To daemonize or not to daemonize",
        type=bool,
        default=False
    )
    parser.set_defaults(subset=True)

    return parser


def main_to_netcdf():
    setup_cli_logger(logging.INFO)

    parser = create_netcdf_arg_parser()
    args = parser.parse_args()

    filter_args = vars(args)
    # Remove non-filter args into positional arguments
    deployments_path = filter_args.pop('deployments_path')
    subset = filter_args.pop('subset')
    daemonize = filter_args.pop('daemonize')
    template = filter_args.pop('template')
    profile_id_type = int(filter_args.pop('profile_id_type'))

    # Move reader_class to a class
    reader_class = filter_args.pop('reader_class')
    if reader_class == 'slocum':
        reader_class = SlocumReader

    if not deployments_path:
        L.error("Please provide a --deployments_path argument or set the "
                "GUTILS_DEPLOYMENTS_DIRECTORY environmental variable")
        sys.exit(parser.print_usage())

    # Add inotify watch
    wm = WatchManager()
    mask = IN_MOVED_TO | IN_CLOSE_WRITE
    wm.add_watch(
        deployments_path,
        mask,
        rec=True,
        auto_add=True
    )

    # Convert ASCII data to NetCDF using a specific reader class
    if reader_class == SlocumReader:
        processor = Slocum2NetcdfProcessor(
            deployments_path=deployments_path,
            subset=subset,
            template=template,
            profile_id_type=profile_id_type,
            prefer_file_filters=True,
            **filter_args
        )
    notifier = Notifier(wm, processor, read_freq=10)
    # Enable coalescing of events. This merges event types of the same type on the same file
    # together over the `read_freq` specified in the Notifier.
    notifier.coalesce_events()

    try:
        L.info(f"Watching {deployments_path} for new ascii files")
        notifier.loop(daemonize=daemonize)
    except NotifierError:
        L.exception('Unable to start notifier loop')
        return 1

    L.info("GUTILS ascii_to_netcdf Exited Successfully")
    return 0
