#!/bin/bash

PATH=/bin:/usr/bin

# Script basename
app=$(basename $0);
# Script absolute directory
app_dir=$(dirname $(which $0));

# Permissions for cac files
cacPerms=664;

# Usage message
USAGE="
$app

NAME
    $app - Convert and merge native Slocum glider binary flight and science data files

SYNOPSIS
    $app [-h] [-f SENSOR_LIST] [-c DBD_CACHE_DIR] [-m] [-e EXTENSION] [-b TWRC_EXE_DIR] [-y PYTHON_PATH] [-g] [-r RANGE] [-q] SOURCEDIR DESTDIR

DESCRIPTION
    Convert and merge all binary *.[demnst]bd files in SOURCEDIR and write the
    output data files to DESTDIR.  If present, the following flight/science
    controller file pairs are merged:

     FLIGHT/SCIENCEsource
     --------------
        dbd/ebd
        mbd/nbd
        sbd/tbd
     --------------

    If the science file is not present, only the flight controller file is
    written.

    -h
        Print help and exit

    -f FILE
        Ignore sensors not contained in FILE, which is a whitespace separated
        file containing sensor names to include

    -c DIRECTORY
        User specified location for writing sensor list .cac files used to
        convert the binary files.  Defaults to SOURCEDIR/cache.  May be
        specified using the DBD_CACHE_DIR environment variable.

    -m
        Output matlab formatted ascii files instead of dba format.

    -e EXTENSION
        Alternate file extension for converted files <Default=dat>

    -b DIRECTORY
        Alternate location containing the Teledyne Webb Research Corporation's
        executables for converting and merging binary *.*bd files to ASCII
        table files.  If not specified, the default versions supplied with
        this package are used.  May be set using the TWRC_EXE_DIR environment
        variable

    -y FILE
        Path to the python interpreter to use for the pseudogram calculations

    -g
        Try to compute pseudograms (can be slow)

    -r RANGE
        echosounder range, used in pseudograms

    -q
        Suppress STDOUT

    -p
        Print the path of each binary file that was successsfully converted to STDOUT

";

# Default option values
# Location of dbd2asc, etc TWRC executables used to convert and merge binary files
local_twrc_exe_dir="$app_dir";
# Default ascii file extension
dba_extension='dat';
# Process options
# while getopts hgqpmf:c:e:b:y::r:: option
# while getopts hf:c:me:b:r:y:qp option
while getopts hf:c:me:b:y::r::giqp option
do

    case "$option" in
        "h")
            # Print help message
            echo "$USAGE";
            exit 0;
            ;;
        "f")
            sensor_filter=$OPTARG;
            ;;
        "c")
            local_cac_dir=$OPTARG;
            ;;
        "m")
            matlab=1;
            ;;
        "e")
            dba_extension=$OPTARG;
            ;;
        "b")
            local_twrc_exe_dir=$OPTARG;
            ;;
        "y")
            python_path=$OPTARG;
            ;;
        "r")
            echosounderRange=$OPTARG;
            ;;
        "g")
            computePseudograms=1;
            ;;
        "i")
            computePseudogramImages=1;
            ;;
        "q")
            quiet=1;
            ;;
        "p")
            printsuccess=1;
            ;;
        "?")
            exit 1;
            ;;
        *)
            echo "Unknown error while processing options";
            exit 1;
            ;;
    esac
done

# Remove options from ARGV
shift $((OPTIND-1));

# Validate executables location
# TWRC_EXE_DIR: use environment variable if set, otherwise use
# $local_twrc_exe_dir which defaults to the app location OR user-specified via
# -b
TWRC_EXE_DIR=${TWRC_EXE_DIR:-$local_twrc_exe_dir};
[ -z "$quiet" ] && echo "TWRC executables directory: $TWRC_EXE_DIR";

if [ ! -d "$TWRC_EXE_DIR" ]
then
    echo "Invalid TWRC_EXE_DIR dir: $TWRC_EXE_DIR" >&2;
    exit 1;
fi

# Make sure the TWRC utilities are available
dbd2asc="${TWRC_EXE_DIR}/dbd2asc";
if [ ! -x "$dbd2asc" ]
then
    echo "Missing utility: $dbd2asc" >&2;
    exit 1;
fi
dbaMerge="${TWRC_EXE_DIR}/dba_merge";
if [ ! -x "$dbaMerge" ]
then
    echo "Missing utility: $dbdMerge" >&2;
    exit 1;
fi
dba2matlab="${TWRC_EXE_DIR}/dba2_orig_matlab";
if [ ! -x "$dba2matlab" ]
then
    echo "Missing utility: $dba2matlab" >&2;
    exit 1;
fi
dba_sensor_filter="${TWRC_EXE_DIR}/dba_sensor_filter";
if [ ! -x "$dba_sensor_filter" ]
then
    echo "Missing utility: $dba_sensor_filter" >&2;
    exit 1;
fi
pseudogram="${TWRC_EXE_DIR}/../echotools/decodePseudogram.py";
if [ ! -x "$pseudogram" ]
then
    echo "Missing pseudogram utility: $pseudogram" >&2;
fi

if [ ! -n "$echosounderRange" ]
then
    echo "Setting default echosounderRange: 60.0" >&2;
    echosounderRange=60.0
fi

# Display usage if no source directory and destination directory were specified
if [ "$#" -eq 0 ]
then
    echo "$USAGE";
    echo "No SOURCEDIR and DESTDIR specified" >&2;
    exit 1;
elif [ "$#" -eq 1 ]
then
    echo "$USAGE";
    echo "DESTDIR not specified" >&2;
    exit 1;
elif [ "$#" -gt 2 ]
then
    echo "$USAGE";
    echo "Please specify a single SOURCEDIR and single DESTDIR" >&2;
    exit 1;
fi

# Set SOURCEDIR and DESTDIR
dbdRoot=$1;
ascDest=$2;

# Get absolute paths for the source and destination directory
dbdRoot=$(readlink -e $dbdRoot);
ascDest=$(readlink -e $ascDest);

# Validate source and destination directories
if [ ! -d "$dbdRoot" ]
then
    echo "$USAGE";
    echo "Invalid SOURCEDIR : $dbdRoot!" >&2;
    exit 1;
fi
if [ ! -d "$ascDest" ]
then
    echo "$USAGE";
    echo "Invalid DESTDIR: $ascDest!" >&2;
    exit 1;
fi

# Display fully qualified path
[ -z "$quiet" ] && echo "Binary source: $dbdRoot";
[ -z "$quiet" ] && echo "ASCII destination: $ascDest";
[ -z "$quiet" ] && echo "sensor_filter: $sensor_filter";
[ -z "$quiet" ] && echo "local_cac_dir: $local_cac_dir";
[ -z "$quiet" ] && echo "matlab: $matlab";
[ -z "$quiet" ] && echo "dba_extension: $dba_extension";
[ -z "$quiet" ] && echo "local_twrc_exe_dir: $local_twrc_exe_dir";
[ -z "$quiet" ] && echo "python_path: $python_path";
[ -z "$quiet" ] && echo "echosounderRange: $echosounderRange";
[ -z "$quiet" ] && echo "computePseudograms: $computePseudograms";
[ -z "$quiet" ] && echo "computePseudogramImages: $computePseudogramImages";
[ -z "$quiet" ] && echo "quiet: $quiet";
[ -z "$quiet" ] && echo "printsuccess: $printsuccess";

# If specified, validate the sensor list for filtering
if [ -n "$sensor_filter" ]
then
    if [ ! -f "$sensor_filter" ]
    then
        echo "Invalid sensor filter list: $sensor_filter!" >&2;
        exit 1;
    else
        # Get absolute path to the sensor filter file if it exists
        sensor_filter=$(readlink -e $sensor_filter);
        [ -z "$quiet" ] && echo "Sensor filter : $sensor_filter";
    fi
fi

# Make a temporary directory for writing the intermediate dba files and
# (optionally) doing the dba->matlab conversions
tmpDir=$(mktemp -d -t ${app}.XXXXXXXXXX);
if [ "$?" -ne 0 ]
then
    echo "Exiting: Can't create temporary dbd directory" >&2;
    exit 1;
fi
[ -z "$quiet" ] && echo "Temp conversion directory: $tmpDir";

# If the location of the .CAC files has not been set, dbd2asc creates a
# directory, 'cache', in the curret working directory.  In this case, we'll
# create this directory in the $dbdRoot directory and explicitly set $CACHE_DIR
# to this location
if [ -z "$local_cac_dir" ]
then
    local_cac_dir=${DBD_CACHE_DIR:-${dbdRoot}/cache};
fi
# Create the sensor list cache directory if it doesn't exist
if [ ! -d "$local_cac_dir" ]
then
    [ -z "$quiet" ] && echo "Creating sensor list cache directory: $local_cac_dir";
    mkdir -m 775 $local_cac_dir;
    [ "$?" -ne 0 ] && exit 1;
fi
local_cac_dir=$(readlink -e $local_cac_dir);
[ -z "$quiet" ] && echo "Sensor list cache directory: $local_cac_dir";

# Change to temporary directory
cd $tmpDir > /dev/null;
# Remove $tmpDir if SIG
trap "{ rm -Rf $tmpDir; exit 255; }" SIGHUP SIGINT SIGKILL SIGTERM SIGSTOP;

# Convert each file individually and move the created files to the location of
# the source binary files
dbdCount=0;
convertedCount=0;
EXIT_STATUS=0;
for dbdSource in $dbdRoot/*
do

    # Files only
    [ ! -f "$dbdSource" ] && continue;

    # File must be of type data
    ftype=$(file $dbdSource | grep data);
    [ -z "$ftype" ] && continue;

    # Strip off extension
    dbdExt=${dbdSource: -3};

    # Get the real filename from the binary file header
    dbdSeg=$(awk '/^full_filename:/ {print tolower($2)}' $dbdSource | sed '{s/-/_/g}');
    # Get the real extension from the binary file header
    fType=$(awk '/^filename_extension:/ {print $2}' $dbdSource);

    # Determine the other file type to look for based on this dbdExtension
    if [ "$dbdExt" == 'SBD' ]
    then
        sciExt='TBD';
    elif [ "$dbdExt" == 'sbd' ]
    then
        sciExt='tbd';
    elif [ "$dbdExt" == 'MBD' ]
    then
        sciExt='NBD';
    elif [ "$dbdExt" == 'mbd' ]
    then
        sciExt='nbd';
    elif [ "$dbdExt" == 'DBD' ]
    then
        sciExt='EBD';
    elif [ "$dbdExt" == 'dbd' ]
    then
        sciExt='ebd';
    else
        # We're only look for d,s or mbd files
        continue;
    fi

    dbdCount=$(( dbdCount + 1 ));

    # dbdSource must have the ascii header line dbd_label: to be a valid *bd
    # file
    is_dbd=$(grep 'dbd_label:' $dbdSource);
    if [ -z "$is_dbd" ]
    then
        echo "Invalid flight source file: $dbdSource" >&2;
        continue;
    fi

    # Check the header of this file and look for the .cac file name:
    # sensor_list_crc:    AAD1AE87
    # We'll need to chmod this file once we've created it to keep from getting
    # annoying permission errors.
    cac=$(awk '/^sensor_list_crc:/ {print tolower($2)}' $dbdSource);

    # Strip the extension off the file to the the segment name
    segment=$(basename $dbdSource .${dbdExt});

    # Echo and suppress the trailing newline
    [ -z "$quiet" ] && echo '----';

    # Append the corresponding science dat file extension to the segment name
    # to create the science data file name.
    sciSource="${dbdRoot}/${segment}.${sciExt}";

    [ -z "$quiet" ] && echo "Source flight file : $dbdSource";

    # Translate all characters in $dbdExt to lowercase for naming the created
    # ascii files
    asciiExt=$(echo $dbdExt | tr [[:upper:]] [[:lower:]]);

    # Create the flight data file .dba filename
    dbdDba=$(mktemp -u ${tmpDir}/${dbdSeg}.dba.XXXXXX.${dbdExt});
#    [ -z "$quiet" ] && echo "Temp flight dba: $dbdDba";

    # Create the flight data file .dat filename, which will be created
    # regardless of whether we're outputting to matlab or ascii format
    datFile="${tmpDir}/${dbdSeg}_${asciiExt}.${dba_extension}";
#    [ -z "$quiet" ] && echo "Temp ASCII destination: $datFile";

    # If the science data file exists, merge $dbdSource and $sciSource and
    # write the output format (dba or matlab).  If it does not exist, just
    # convert $dbdSource and write to the output format (dba or matlab)
    if [ -f "$sciSource" ]
    then

	    # dbdSource must have the ascii header line dbd_label: to be a valid *bd
	    # file
	    is_dbd=$(grep 'dbd_label:' $sciSource);
	    if [ -z "$is_dbd" ]
	    then
	        echo "Invalid science source file: $sciSource" >&2;
	        continue;
	    fi

        [ -z "$quiet" ] && echo "Source science file: $sciSource";

        if [ -n "$computePseudograms" ]
        then
            [ -z "$quiet" ] && echo "Computing Scatter Pseudogram";
            $python_path $pseudogram \
                --inpDir ${dbdRoot} \
                --inpFile ${segment}.?bd \
                --cacheDir $local_cac_dir \
                --dbd2asc $dbd2asc \
                --csvOut "${tmpDir}/${dbdSeg}_${asciiExt}.pseudogram" \
                --csvHeader \
                --echosounderRange "$echosounderRange" \
                --title "$segment - Scatter"
        fi

        if [ -n "$computePseudogramImages" ]
        then
            [ -z "$quiet" ] && echo "Computing Pseudogram Images";
            $python_path $pseudogram \
                --inpDir ${dbdRoot} \
                --inpFile ${segment}.?bd \
                --cacheDir $local_cac_dir \
                --dbd2asc $dbd2asc \
                --imageOut "${tmpDir}/${dbdSeg}_${asciiExt}.scatter.png" \
                --useScatterPlot \
                --echosounderRange "$echosounderRange" \
                --title "$segment - Scatter"

            $python_path $pseudogram \
                --inpDir ${dbdRoot} \
                --inpFile ${segment}.?bd \
                --cacheDir $local_cac_dir \
                --dbd2asc $dbd2asc \
                --imageOut "${tmpDir}/${dbdSeg}_${asciiExt}.binned.png" \
                --echosounderRange "$echosounderRange" \
                --title "$segment - Binned"
        fi

        [ -z "$quiet" ] && echo "Converting & Merging flight and science files";

        # Create the science data file .dba filename
        sciDba="${tmpDir}/${dbdSeg}_${sciExt}.dba";
        sciDba="${tmpDir}/$(basename $dbdDba .${dbdExt}).${sciExt}";

#        [ -z "$quiet" ] && echo "Temp science dba: $sciDba";

        # Convert the $dbdSource binary to ascii and write to *.dba file
        if [ -n "$sensor_filter" ]
        then
            # Filter the sensors that will go into the file
            $dbd2asc -o \
                -c $local_cac_dir \
                $dbdSource | \
                $dba_sensor_filter -f $sensor_filter \
                > $dbdDba;
        else
            # Include all sensors
            $dbd2asc -o \
                -c $local_cac_dir \
                $dbdSource > \
                $dbdDba;
        fi

        # Exit status == 0 if successful or 1 if failed.  If failure, $dbdDba
        # will be empty, so we need to remove it
        if [ "$?" -ne 0 ]
        then
            echo "Skipping segment: $segment" >&2;
            rm $dbdDba;
            EXIT_STATUS=1;
            continue;
        fi

        # Convert the $sciSource binary to ascii and write to *.dba file
        if [ -n "$sensor_filter" ]
        then
            # Filter the sensors that will go into the file
            $dbd2asc -o \
                -c $local_cac_dir \
                $sciSource | \
                $dba_sensor_filter -f $sensor_filter \
                > $sciDba;
        else
            # Include all sensors
            $dbd2asc -o \
                -c $local_cac_dir \
                $sciSource > \
                $sciDba;
        fi

        # Exit status == 0 if successful or 1 if failed.  If failed, $sciDba
        # will be empty, so we need to remove it.  Since the science data file
        # conversion failed, continue on but write ONLY the flight controller
        # data to the output destination
        if [ "$?" -ne 0 ]
        then
            [ -z "$quiet" ] && echo "Science conversion failed: Writing flight controller data ONLY...";
            rm $sciDba;
            EXIT_STATUS=1;
        fi

        # Finally, write the file to the desired output format.  If $matlab is
        # set, use dba2_orig_matlab to create the .m and .dat files.  If it is
        # not set, move $dbaOut to the same filename, but with a .dat
        # extension
        if [ -n "$matlab" ]
        then
            # If successful, the output of this command is the name of the
            # file that was created
            if [ -f "$sciDba" ]
            then
                mFile="${tmpDir}/$($dbaMerge $dbdDba $sciDba | $dba2matlab)";
                EXIT_STATUS=1;
            else
                mFile=$(cat $dbdDba | $dba2matlab);
            fi

            # Skip to the next file if an error occurred
            if [ ! -f "$mFile" ]
            then
                EXIT_STATUS=1;
                continue;
            fi

            [ -z "$quiet" ] && echo "M-File Created: $mFile";

            # Increment the successful file counter if both $datFile and
            # $mFile exist
            convertedCount=$(( convertedCount + 1 ));

            # Delete the individual dba files
            rm $dbdDba;
            [ -f "$sciDba" ] && rm $sciDba;

        else
            if [ -f "$sciDba" ]
            then
                $dbaMerge $dbdDba $sciDba > $datFile;

                # Skip to the next file if an error occurred
                if [ "$?" -ne 0 ]
                then
                    EXIT_STATUS=1;
                    continue
                fi

            else
                cat $dbdDba > $datFile;
            fi

            [ -z "$quiet" ] && echo "Output File Created: $datFile";

            # Increment the successful file counter if the move was successful
            convertedCount=$(( convertedCount + 1 ));

            # Delete the individual dba files
            rm $dbdDba $sciDba;

        fi

        [ -n "$printsuccess" ] && echo -e "$dbdSource\n$sciSource\n$datFile";

    else
        [ -z "$quiet" ] && echo "Converting flight data file ONLY";

        # Convert to ascii and write to *.dba file
        if [ -n "$sensor_filter" ]
        then
            # Filter the sensors that will go into the file
            $dbd2asc -o \
                -c $local_cac_dir \
                $dbdSource | \
                $dba_sensor_filter -f $sensor_filter \
                > $dbdDba;
        else
            # Include all sensors
            $dbd2asc -o \
                -c $local_cac_dir \
                $dbdSource > \
                $dbdDba;
        fi

        # Exit status == 0 if successful or 1 if failed
        if [ "$?" -ne 0 ]
        then
            echo "Skipping segment: $segment" >&2;
            rm $dbdDba;
            EXIT_STATUS=1;
            continue;
        fi

        # Finally, write the file to the desired output format.  If $matlab is
        # set, use dba2_orig_matlab to create the .m and .dat files.  If it is
        # not set, move $dbaOut to the same filename, but with a .dat
        # extension
        if [ -n "$matlab" ]
        then
            # If successful, the output of this command is the name of the
            # file that was created
            mFile="${tmpDir}/$(cat $dbdDba | $dba2matlab)";
            # Remove the dba file
            rm $dbdDba;

            # Skip to the next file if an error occurred
            if [ ! -f "$mFile" ]
            then
                EXIT_STATUS=1;
                continue;
            fi

            [ -z "$quiet" ] && echo "M-File Created: $mFile";

            # Increment the successful file counter if both $datFile and
            # $mFile exist
            convertedCount=$(( convertedCount + 1 ));

        else
            mv $dbdDba $datFile;

            # Skip to the next file if an error occurred
            if [ "$?" -ne 0 ]
            then
                EXIT_STATUS=1;
                continue;
            fi

            [ -z "$quiet" ] && echo "Output File Created: $datFile";

            # Increment the successful file counter if the move was successful
            convertedCount=$(( convertedCount + 1 ));
        fi

        [ -n "$printsuccess" ] && echo -e "$dbdSource\n$datFile";

    fi

    # If successful, change the permissions on the .cac file to rwx for
    # owner and group
    cacFile="${local_cac_dir}/${cac}.cac";
    if [ -f "$cacFile" ]
    then
        [ -z "$quiet" ] && echo "Updating $asciiExt ${cac}.cac permissions ($cacPerms)";
        oldPerms=$(stat --format=%a $cacFile);
        chmod $cacPerms $cacFile;
        newPerms=$(stat --format=%a $cacFile);
    fi

    # Search for the .cac file for the science binary to change
    # permissions on this one as well
    if [ -f "$sciSource" ]
    then
        sciCac=$(awk '/^sensor_list_crc:/ {print tolower($2)}' $sciSource);
        sciCacFile="${local_cac_dir}/${sciCac}.cac";
        if [ -f "$sciCacFile" ]
        then
            [ -z "$quiet" ] && echo "Updating $sciExt ${sciCac}.cac permissions ($cacPerms)";
            oldPerms=$(stat --format=%a $sciCacFile);
            chmod $cacPerms $sciCacFile;
            newPerms=$(stat --format=%a $sciCacFile);
        fi
    fi

done

[ -z "$quiet" ] && echo '==============================================================================';
[ -z "$quiet" ] && echo "$convertedCount/$dbdCount files successfully converted.";
[ -z "$quiet" ] && echo '=============================================================================='

# Move all remaining file in $tmpDir to $ascDest
# Default exit status
[ "$convertedCount" -eq 0 ] && exit 1;

[ -z "$quiet" ] && echo -n "Moving output files to destination: $ascDest...";
status=$(mv ${tmpDir}/*.${dba_extension} ${tmpDir}/*.pseudogram ${tmpDir}/*.png $ascDest 2>&1);
if [ "$?" -eq 0 ]
then
    [ -z "$quiet" ] && echo "Done.";
    # Set status to 0 to signal successful conversion
    STATUS=0;
else
    [ -z "$quiet" ] && echo "Failed.";
fi
[ -z "$quiet" ] && echo '=============================================================================='

# Remove $tmpDir
rm -Rf $tmpDir;

exit $STATUS;
