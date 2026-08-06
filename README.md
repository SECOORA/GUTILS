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

## Submitting to a Data Assembly Center (FTP / FTPS)

The `gutils_netcdf_to_ftp_watch` service watches the deployments directory and
uploads finished netCDF profiles to a data assembly center. Data assembly
centers are migrating from plain FTP to FTPS, and they are not all moving at the
same time, so GUTILS negotiates the protocol per connection rather than being
configured for one or the other.

Configure it with:

| Variable | Meaning |
| --- | --- |
| `GUTILS_FTP_URL` | Hostname of the DAC endpoint |
| `GUTILS_FTP_USER` | Account name (defaults to `anonymous`) |
| `GUTILS_FTP_PASS` | Password (defaults to empty) |
| `GUTILS_FTPS_NO_VERIFY` | If set, skip TLS certificate verification |

On each upload GUTILS attempts explicit FTPS (`AUTH TLS`) first, and only falls
back to plaintext FTP if the server replies that TLS is not supported. It also
resumes the control connection's TLS session on the data connection, which
vsftpd (`require_ssl_reuse=YES`, its default) and IIS require and which Python's
standard library does not do on its own.

Current limitations to be aware of when a center gives you new details:

* Only explicit FTPS on port 21 is supported. Implicit FTPS on port 990 is not,
  and the port is not configurable.
* `GUTILS_FTP_URL` is a bare hostname, not a URL — an `ftps://host` value will
  not work.
* The fallback to plaintext is automatic and is not logged. There is no way yet
  to say "this center has finished migrating, never downgrade".
* `GUTILS_FTPS_NO_VERIFY` is checked for presence only, so setting it to `0` or
  `false` still disables verification. Leave it unset in production.

### Checking a data assembly center's FTP service

Every center configures its service differently, and the failure modes look
alike from the outside: a TLS session reuse requirement, a passive port range
blocked by a firewall, and an untrusted certificate all present as "the upload
hung". `extras/ftps_check.py` probes an endpoint and reports what it actually
supports, so a center can be assessed before its migration rather than
debugged after it.

```bash
python extras/ftps_check.py -H ftp.example.org -u myuser
```

It uses only the Python standard library, so it can be run from a plain Python
3 interpreter without the `gutils` environment. It prompts for the password;
use `--password-env VAR` to read it from the environment for unattended runs.

The checks are read-only — the data channel is exercised with `LIST`, never an
upload, unless you pass `--upload`, which writes a small file and then deletes
it.

The report covers TCP reachability, the pre-authentication `FEAT` feature list
(the reliable way to learn whether `AUTH TLS` is offered), whether certificates
verify against the system trust store, the negotiated protocol and cipher, the
passive address the server advertises, and whether the data channel requires TLS
session reuse.

The exit status summarizes the result:

| Status | Meaning |
| --- | --- |
| `0` | FTPS works against this host with a stock client |
| `1` | FTPS works, but only with TLS session reuse |
| `2` | FTPS is not usable against this host |

By default the tool also attempts a cleartext login to determine whether the
host still permits a downgrade. **This transmits the password in the clear.**
Pass `--ftps` to skip the plaintext checks and exercise FTPS only.

Useful options:

* `-p / --port` — port to connect to (default `21`)
* `-k / --insecure` — disable certificate verification for the functional
  checks; certificate verification is still reported separately
* `-t / --timeout` — socket timeout in seconds (default `30`)
* `--upload [NAME]` — additionally perform a write test

## Docker Compose

This repository includes a docker-compose.yml file which has services useful for
testing integration of `gutils` with external services and data parsing.

To use the docker compose services, copy `.env.template` to `.env` and update
the variables to point at the correct data volume and values.

Then run:

```
make docker-local
docker compose up -d
```

This will build the project and make the `gutils` docker image available to
docker-compose. Then this will launch the services.

## Testing

The tests are written using `pytest`. To run the tests use the `pytest` command.

To run the "long" tests you will need [this](https://github.com/SECOORA/SGS) cloned somewhere. Then set the env variable `GUTILS_TEST_CONFIG_DIRECTORY` to the config directory, ie `export GUTILS_TEST_CONFIG_DIRECTORY=/data/dev/SGS/config` and run `pytest -m long`

To run a specific test, locate the test name you would like to run and run: `pytest -k [name_of_test]` i.e. `pytest -k TestEcoMetricsOne`

To run the tests in Docker, you can build the image (which does not include the tests or test data to reduce image size) and volume mount the tests when running:

```shell
docker build -t gutils .
docker run -it --rm -v $(pwd)/gutils/tests:/code/gutils/tests gutils pytest -m "not long"
```


## Integration Testing

If your glider data mount is at `/mnt/gliders` then these commands demonstrate
how to set up a deployment with glider data to test processing and FTP upload.

```
mkdir -p /mnt/gliders/{ftp,ftps,ftps-noreuse,secoora/dev/deployments}
tar -C /mnt/gliders/secoora/dev/deployments/ -zxvf extras/salacia-20260731T0000.tar.gz
```

Running `docker compose logs -f gutils` should show the watchers identify the
files, parse them into ASCII, then create the netCDF profiles and upload those
to the FTPS service. Those profiles will then be available in
`/mnt/gliders/ftps-noreuse/`

The `integration-test` make target does all of the above for you:

```
make integration-test
```

It builds the image, creates the directories under `GLIDER_DATA_VOLUME` (read
from `.env`, defaulting to `/mnt/gliders`), brings up the compose services,
waits for them to settle, and then extracts the newest
`extras/salacia*.tar.gz` into the deployments directory so the watchers pick it
up. If the FTPS services are slow to generate their certificates, give them
longer with `INTEGRATION_WAIT=30 make integration-test`.
