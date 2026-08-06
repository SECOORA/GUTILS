FROM phusion/baseimage:focal-1.2.0

LABEL org.opencontainers.image.description="GUTILS as a docker image."
LABEL org.opencontainers.image.authors="Kyle Wilcox <kyle@axiomdatascience.com>"
LABEL org.opencontainers.image.url="https://git.axiom/SECOORA/GUTILS/"
LABEL org.opencontainers.image.source="https://git.axiom/SECOORA/GUTILS/"
LABEL org.opencontainers.image.licenses="MIT"


# Use baseimage-docker's init system
CMD ["/sbin/my_init"]
ENV KILL_PROCESS_TIMEOUT=30
ENV KILL_ALL_PROCESSES_TIMEOUT=30

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

RUN apt-get update && apt-get install -y \
        binutils \
        build-essential \
        bzip2 \
        ca-certificates \
        file \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        pwgen \
        wget \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Setup CONDA (https://hub.docker.com/r/continuumio/miniconda3/~/dockerfile/)
ENV MINICONDA_VERSION=py39_4.11.0
ENV MINICONDA_SHA256=4ee9c3aa53329cd7a63b49877c0babb49b19b7e5af29807b793a76bdb1d362b4
RUN curl -k -o /miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-$MINICONDA_VERSION-Linux-x86_64.sh && \
    echo $MINICONDA_SHA256 /miniconda.sh | sha256sum --check && \
    /bin/bash /miniconda.sh -b -p /opt/conda && \
    rm /miniconda.sh && \
    /opt/conda/bin/conda update -c conda-forge -n base conda && \
    /opt/conda/bin/conda clean -afy && \
    /opt/conda/bin/conda init && \
    find /opt/conda/ -follow -type f -name '*.a' -delete && \
    find /opt/conda/ -follow -type f -name '*.js.map' -delete && \
    /opt/conda/bin/conda install -y -c conda-forge -n base mamba && \
    /opt/conda/bin/conda clean -afy

ENV PATH=/opt/conda/bin:$PATH

COPY environment.yml /tmp/environment.yml
RUN mamba env update \
        -n base \
        -f /tmp/environment.yml \
        && \
    mamba clean -afy

COPY pip-requirements.txt /tmp/pip-requirements.txt
RUN pip install \
        --no-deps \
        --force-reinstall \
        --ignore-installed \
        --use-pep517 \
        -r /tmp/pip-requirements.txt

ENV PATH=/opt/conda/bin:$PATH

ENV GUTILS_DEPLOYMENTS_DIRECTORY=/gutils/deployments
ENV GUTILS_ERDDAP_CONTENT_PATH=/gutils/erddap/content
ENV GUTILS_ERDDAP_FLAG_PATH=/gutils/erddap/flag
VOLUME ["${GUTILS_DEPLOYMENTS_DIRECTORY}", "${GUTILS_ERDDAP_CONTENT_PATH}", "${GUTILS_ERDDAP_FLAG_PATH}"]

RUN mkdir -p /etc/my_init.d && \
    mkdir -p /gutils
COPY docker/init/* /etc/my_init.d/

ENV GUTILS_VERSION=3.2.0

ENV PROJECT_ROOT=/code
RUN mkdir -p "$PROJECT_ROOT"
COPY . $PROJECT_ROOT
RUN cd $PROJECT_ROOT && pip install --no-deps --use-pep517 .
WORKDIR $PROJECT_ROOT
