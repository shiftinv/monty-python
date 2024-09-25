FROM mcr.microsoft.com/devcontainers/python:3.11

RUN apt-get update \
 && apt-get install bash-completion \
 && rm -rf /var/lib/apt/lists/*


# Set pip to have cleaner logs and no saved cache
ENV PIP_NO_CACHE_DIR=false

# Create the working directory
WORKDIR /workspace

# Install project dependencies
RUN pip install -U pip wheel setuptools
RUN pipx install poetry~=1.7.0

RUN poetry config virtualenvs.in-project true

# Set SHA build argument
ARG git_sha="main"
ENV GIT_SHA=$git_sha
