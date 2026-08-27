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

# EPEL and CRB carry the RHEL equivalents of a good part of GitHub's toolset — ShellCheck,
# aria2, patchelf, upx, sshpass and others live there rather than in the base repositories.
RUN dnf install -y --nodocs epel-release dnf-plugins-core \
    && dnf config-manager --set-enabled crb \
    && dnf clean all

# The same toolset GitHub installs on its ubuntu images
# (actions/runner-images: images/ubuntu/toolsets/toolset-2404.json), mapped to RHEL package
# names and grouped the same way, so the two images can be diffed against each other.
#
# --allowerasing is needed for curl: RHEL 9 ships curl-minimal, which conflicts with the full
# package workflows expect.
#
# Left out because they cannot work in a container: systemd-coredump, pollinate, haveged.
# Left out because RHEL has no packaging for them: mediainfo and sphinxsearch (RPM Fusion
# only). A workflow needing those should use the Ubuntu variant.
RUN dnf install -y --allowerasing --setopt=install_weak_deps=False --nodocs \
        `# vital_packages` \
        bzip2 curl gcc gcc-c++ jq make tar unzip wget \
        `# common_packages` \
        autoconf automake bind-utils dbus dpkg fakeroot glibc-langpack-en gnupg2 \
        google-noto-emoji-color-fonts iproute iputils libicu-devel libtool libyaml-devel \
        mercurial openssh-clients openssl-devel p7zip-plugins pkgconf-pkg-config rpm \
        sqlite-devel texinfo tk tree tzdata xz zsync \
        `# cmd_packages` \
        acl aria2 binutils bison brotli coreutils file findutils flex ftp lz4 m4 net-tools \
        nmap-ncat nss-tools parallel patchelf pigz rsync ShellCheck sqlite sshpass \
        sudo swig telnet time zip \
        `# not upstream, but a runner without them is surprising` \
        ca-certificates cmake git gzip hostname procps-ng python3 python3-pip shadow-utils \
        which \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# Packages that were renamed or dropped between RHEL 9 and RHEL 10. Both spellings are listed
# and `strict=0` takes whichever exists, so one Dockerfile serves both releases.
#
#   p7zip, p7zip-plugins -> 7zip                         (renamed in RHEL 10)
#   google-noto-emoji-color-fonts -> google-noto-color-emoji-fonts
#   upx, xorg-x11-server-Xvfb                            (dropped in RHEL 10, no replacement)
#
# The result is printed rather than assumed: an image quietly missing a tool is how a
# workflow ends up failing for a reason nobody can see.
RUN dnf install -y --setopt=strict=0 --setopt=install_weak_deps=False --nodocs \
        7zip p7zip p7zip-plugins \
        google-noto-color-emoji-fonts google-noto-emoji-color-fonts \
        upx xorg-x11-server-Xvfb \
    ; dnf clean all && rm -rf /var/cache/dnf \
    && echo "optional tools present:" \
    && for t in 7z upx Xvfb; do \
           printf '  %-6s %s\n' "$t" "$(command -v $t || echo 'not available on this release')"; \
       done

# Node and npm, the same major version as the Ubuntu image. GitHub keeps these in a
# toolcache; here they are installed, because a workflow running `npm ci` without
# actions/setup-node is common and the failure is obscure. Other toolchains are not
# preinstalled: actions/setup-python, setup-go and setup-java fetch what they need.
ARG NODE_MAJOR=22
RUN curl -fsSL "https://rpm.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && dnf install -y --nodocs nodejs \
    && npm --version \
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


# pipx, laid out the way GitHub's images lay it out (PIPX_HOME=/opt/pipx,
# PIPX_BIN_DIR=/opt/pipx_bin on PATH), because workflows written against those images do
# `pipx install poetry` and expect it to be there.
#
# Owned by the runner user, unlike upstream: a job installing a tool at runtime has to be
# able to write here, and jobs do not run as root.
ENV PIPX_HOME=/opt/pipx \
    PIPX_BIN_DIR=/opt/pipx_bin \
    PATH=/opt/pipx_bin:$PATH

RUN dnf install -y --nodocs pipx \
    && dnf clean all && rm -rf /var/cache/dnf \
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
