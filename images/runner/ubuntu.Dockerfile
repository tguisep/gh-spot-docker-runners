# syntax=docker/dockerfile:1

# The GitHub Actions runner on Ubuntu, and nothing clever.
#
# This image holds no credentials and makes no API calls. It receives a single-use
# just-in-time configuration in RUNNER_JIT_CONFIG, runs one job, and exits. Everything that
# decides *whether* a runner should exist lives in the daemon on the host, where it can be
# tested.
ARG UBUNTU_VERSION=24.04
FROM ubuntu:${UBUNTU_VERSION}

# Pinned explicitly rather than resolved at build time: a reproducible image matters more
# than being current, and the daemon reports when a newer runner is available.
ARG RUNNER_VERSION=2.336.0
ARG RUNNER_SHA256_X64=04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d
ARG RUNNER_SHA256_ARM64=58b758e420b87093fbd4bfddd368074960053e2f1388f01848c82624b90f27d1

# The host's docker group id, so the mounted socket is usable without loosening its mode.
ARG DOCKER_GID=999

ENV DEBIAN_FRONTEND=noninteractive \
    RUNNER_MANUALLY_TRAP_SIG=1 \
    ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1

# The toolset GitHub installs on its own ubuntu images, from
# actions/runner-images: images/ubuntu/toolsets/toolset-2404.json.
#
# Grouped as they are upstream, so a diff against a future toolset stays readable. Three
# packages are deliberately left out because they cannot work in a container:
# systemd-coredump (drags in systemd), pollinate (a boot-time entropy service) and haveged
# (an entropy daemon the kernel has not needed for years).
RUN apt-get update && apt-get install -y --no-install-recommends \
    `# vital_packages` \
        bzip2 curl g++ gcc jq make tar unzip wget \
    `# common_packages` \
        autoconf automake dbus dnsutils dpkg dpkg-dev fakeroot fonts-noto-color-emoji \
        gnupg2 iproute2 iputils-ping libicu-dev libsqlite3-dev libssl-dev libtool \
        libyaml-dev locales mercurial openssh-client p7zip-rar pkg-config \
        python-is-python3 rpm texinfo tk tree tzdata upx xvfb xz-utils zsync \
    `# cmd_packages` \
        acl aria2 binutils bison brotli coreutils file findutils flex ftp libnss3-tools \
        lz4 m4 mediainfo net-tools netcat-openbsd p7zip-full parallel patchelf pigz rsync \
        shellcheck sphinxsearch sqlite3 ssh sshpass sudo swig telnet time zip \
    `# not upstream, but a runner without them is surprising: git for checkout, and pip and` \
    `# venv because Debian ships python3 without ensurepip` \
        ca-certificates cmake git python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Node and npm. GitHub keeps these in a toolcache; here they are just installed, because a
# workflow running `npm ci` without actions/setup-node is common and the failure is obscure.
# Other toolchains are not preinstalled: actions/setup-python, setup-go, setup-java and the
# rest download what they need at runtime, so they work on this image already.
ARG NODE_MAJOR=22
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm --version \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI only. The daemon is the host's, reached through the mounted socket.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin \
        docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# The mounted socket is only usable by the unprivileged runner if this group id matches the
# host's. `groupadd || true` is not enough: the Docker CE RPM creates a `docker` group during
# install, so the add silently fails and the id is whatever the package chose. Force it.
RUN if getent group docker >/dev/null; then \
        groupmod -o -g "${DOCKER_GID}" docker; \
    else \
        groupadd -o -g "${DOCKER_GID}" docker; \
    fi \
    && useradd -m -s /bin/bash runner \
    && usermod -aG docker runner \
    && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner \
    && test "$(getent group docker | cut -d: -f3)" = "${DOCKER_GID}" \
       || { echo "docker gid is not ${DOCKER_GID}" >&2; exit 1; }

WORKDIR /home/runner

# Verified before extraction: this is the one artefact in the image that isn't from a signed
# apt repository.
RUN ARCH="$(dpkg --print-architecture)" \
    && case "${ARCH}" in \
         amd64) RUNNER_ARCH=x64;   RUNNER_SHA256="${RUNNER_SHA256_X64}" ;; \
         arm64) RUNNER_ARCH=arm64; RUNNER_SHA256="${RUNNER_SHA256_ARM64}" ;; \
         *) echo "unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o runner.tar.gz \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  runner.tar.gz" | sha256sum -c - \
    && tar xzf runner.tar.gz \
    && rm runner.tar.gz \
    && ./bin/installdependencies.sh \
    && rm -rf /var/lib/apt/lists/*


# pipx, laid out the way GitHub's images lay it out (PIPX_HOME=/opt/pipx,
# PIPX_BIN_DIR=/opt/pipx_bin on PATH), because workflows written against those images do
# `pipx install poetry` and expect it to be there.
#
# Owned by the runner user, unlike upstream: a job installing a tool at runtime has to be
# able to write here, and jobs do not run as root.
ENV PIPX_HOME=/opt/pipx \
    PIPX_BIN_DIR=/opt/pipx_bin \
    PATH=/opt/pipx_bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends pipx \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o runner -g runner "${PIPX_HOME}" "${PIPX_BIN_DIR}" \
    && pipx --version

# Marks this as an image the daemon needs. Housekeeping prunes unused images on the host,
# and without a marker it would eventually reclaim the very images runners start from —
# leaving the daemon unable to launch anything until they were rebuilt.
LABEL io.ghspot.image=runner

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /home/runner/_work /home/runner/.cache \
    && chown -R runner:runner /home/runner

USER runner

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
