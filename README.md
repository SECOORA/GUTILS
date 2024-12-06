# 🚤 Glider Utilities (GUTILS)

[![license](https://img.shields.io/github/license/SECOORA/GUTILS.svg)](https://github.com/SECOORA/GUTILS/blob/master/LICENSE.txt)
[![GitHub release](https://img.shields.io/github/release/SECOORA/GUTILS.svg)]()

🐍 + 🌊 + 🚤

A python framework for working with the data from Autonomous Underwater Vehicles (AUVs)

Supports:

+  Teledyne Webb Slocum Gliders

The main concept is to break the data down from each deployment of glider into different states:

* Raw / Binary data
  * Slocum: `rt` (`.tbd`, `.sbd`, `.mbd`, and `.nbd`) and `delayed` (`.ebd` and `.dbd`)
* ASCII data
  * Using tools provided by vendors and/or python code, an ASCII representation of the dataset should be able to be analyzed using open tools and software libraries. GUTILS provides functions to convert Raw/Binary data into an ASCII representation on disk.
* Standardized DataFrame
  * Once in an ASCII representation, GUTILS provides methods to standardize the ASCII data into a pandas DataFrame format with well-known column names and metadata. All analysis and computations are done in the pandas ecosystem at this stage, such as computing profiles and other variables based on the data. This in an in-memory state.
* NetCDF
  * After analysis and computations are complete, GUTILS can serialize the DataFrame to a netCDF file format that is compatible with the IOOS Glider DAC profile netCDF format. GUTILS provides metadata templates to make sure metadata is captured correctly the output netCDF files.


## Resources

+  **Documentation:** https://secoora.github.io/GUTILS/docs/
+  **API:** https://secoora.github.io/GUTILS/docs/api/gutils.html
+  **Source Code:** https://github.com/secoora/GUTILS/
+  **Git clone URL:** https://github.com/secoora/GUTILS.git


## Installation

GUTILS is available as a python library through [`conda`](http://conda.pydata.org/docs/install/quick.html) and was designed for Python 3.8+.

```bash
$ conda create -n gutils python=3.9
$ source activate gutils
$ conda install -c conda-forge gutils
```

## Development

## Setup

```bash
$ git clone https://github.com/secoora/GUTILS.git
```

Install Anaconda (using python3): http://conda.pydata.org/docs/download.html

Read Anaconda quickstart: http://conda.pydata.org/docs/test-drive.html

It is recommended that you use `mamba` to install to speed up the process: https://github.com/mamba-org/mamba.

Setup a GUTILS conda environment and install the base packages:
you are
```bash
$ mamba env create environment.yml
$ conda activate gutils
```

## Update

To update the gutils environment, issue these commands from your root gutils directory

```bash
$ git pull
$ conda deactivate
$ conda env remove -n gutils
$ mamba env create environment.yml
$ conda activate gutils
```

## Testing

The tests are written using `pytest`. To run the tests use the `pytest` command.

To run the "long" tests you will need [this](https://github.com/SECOORA/SGS) cloned somewhere. Then set the env variable `GUTILS_TEST_CONFIG_DIRECTORY` to the config directory, ie `export GUTILS_TEST_CONFIG_DIRECTORY=/data/dev/SGS/config` and run `pytest -m long`

To run a specific test, locate the test name you would like to run and run: `pytest -k [name_of_test]` i.e. `pytest -k TestEcoMetricsOne`

To run the tests in Docker, you can build the image (which does not include the tests or test data to reduce image size) and volume mount the tests when running:

```shell
docker built -t gutils .
docker run -it --rm -v $(pwd)/gutils/tests:/code/gutils/tests gutils pytest -m "not long"
```
