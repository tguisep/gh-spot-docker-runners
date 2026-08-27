# syntax=docker/dockerfile:1

# The GitHub Actions runner on the RHEL family, and nothing clever.
#
# Same contract as the Ubuntu image: no credentials, no API calls of its own. It receives a
# single-use just-in-time configuration in RUNNER_JIT_CONFIG, runs one job, and exits.
#
# BASE_IMAGE selects the rebuild. AlmaLinux is the default because it is a faithful RHEL
# rebuild with complete repositories and no subscription. Also valid:
#
#   rockylinux/rockylinux:9                   another RHEL rebuild
#   quay.io/centos/centos:stream9             upstream of the next RHEL minor
#   registry.access.redhat.com/ubi9/ubi       Red Hat's own, but a reduced package set
#
# Jobs that must run on genuine RHEL should use the UBI base and accept that some packages
# a workflow expects will not be in its repositories.
ARG BASE_IMAGE=almalinux:9
FROM ${BASE_IMAGE}

# Pinned explicitly rather than resolved at build time: a reproducible image matters more
# than being current, and the daemon reports when a newer runner is available.
ARG RUNNER_VERSION=2.336.0
ARG RUNNER_SHA256_X64=04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d
ARG RUNNER_SHA256_ARM64=58b758e420b87093fbd4bfddd368074960053e2f1388f01848c82624b90f27d1

# The host's docker group id, so the mounted socket is usable without loosening its mode.
ARG DOCKER_GID=999

ENV RUNNER_MANUALLY_TRAP_SIG=1 \
    ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1

# `dnf` on a minimal base does not install documentation or weak dependencies, which keeps
# the image close in size to the Ubuntu one.
#
# --allowerasing is needed for curl: RHEL 9 ships curl-minimal, which conflicts with the full
# package. Workflows expect the full curl that GitHub's own images provide, so the minimal
# one is replaced rather than kept.
RUN dnf install -y --allowerasing --setopt=install_weak_deps=False --nodocs \
        ca-certificates \
        curl \
        findutils \
        git \
        gzip \
        hostname \
        jq \
        openssl \
        procps-ng \
        rsync \
        shadow-utils \
        sudo \
        tar \
        unzip \
        which \
        zip \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# Docker CLI only. The daemon is the host's, reached through the mounted socket.
# The upstream Docker repository serves CentOS builds, which the RHEL rebuilds consume.
RUN dnf install -y --setopt=install_weak_deps=False --nodocs dnf-plugins-core \
    && dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo \
    && dnf install -y --setopt=install_weak_deps=False --nodocs \
        docker-ce-cli docker-buildx-plugin docker-compose-plugin \
    && dnf clean all \
    && rm -rf /var/cache/dnf

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
# package repository.
RUN ARCH="$(uname -m)" \
    && case "${ARCH}" in \
         x86_64)  RUNNER_ARCH=x64;   RUNNER_SHA256="${RUNNER_SHA256_X64}" ;; \
         aarch64) RUNNER_ARCH=arm64; RUNNER_SHA256="${RUNNER_SHA256_ARM64}" ;; \
         *) echo "unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o runner.tar.gz \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  runner.tar.gz" | sha256sum -c - \
    && tar xzf runner.tar.gz \
    && rm runner.tar.gz \
    && ./bin/installdependencies.sh \
    && dnf clean all \
    && rm -rf /var/cache/dnf

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /home/runner/_work /home/runner/.cache \
    && chown -R runner:runner /home/runner

USER runner

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
