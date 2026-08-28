# syntax=docker/dockerfile:1

# The runner image for a Jetson on JetPack 4 (L4T r32).
#
# A thin layer on the Ubuntu variant rather than a base of its own. Everything a job needs —
# the toolset, the runner payload, the unprivileged user — is already correct there; the only
# thing a Jetson adds is finding the GPU.
#
# Why not nvcr.io/nvidia/l4t-base:r32.7.1, the obvious base: it is Ubuntu 18.04, and the
# GitHub Actions runner has shipped .NET 8 since v2.317, which needs glibc 2.28. Ubuntu 18.04
# has 2.27. The host's 18.04 userspace does not constrain us — a container brings its own.
#
# Why 22.04 and not 20.04, which is nearer the board's own release and would seem the safer
# hedge: focal ships Python 3.8, and the toolset contract every variant is held to includes
# `pipx install poetry`, which needs 3.9. A base that cannot pass verify.sh is not a base.
# The injected driver is the old side of the pairing either way, and old libraries under a
# newer glibc is the direction that works.
ARG BASE_IMAGE=ghspot/runner:ubuntu-22.04
FROM ${BASE_IMAGE}

USER root

# JetPack does not put a driver in the image. `nvidia-container-runtime` bind-mounts the
# host's Tegra userspace driver in at container start, into these two directories, driven by
# the CSV lists in /etc/nvidia-container-runtime/host-files-for-container.d on the host.
#
# Both mechanisms below are needed, and neither is redundant:
#
#   ld.so.conf.d     for anything that runs `ldconfig` itself, and for readability
#   LD_LIBRARY_PATH  because /etc/ld.so.cache is generated at build time, when these
#                    directories are still empty. ld.so consults the cache, then only its
#                    trusted defaults — a path listed in ld.so.conf whose contents appeared
#                    afterwards is never searched. Without this, libcuda.so.1 is mounted
#                    into the container and still not found.
RUN printf '%s\n' \
        /usr/lib/aarch64-linux-gnu/tegra \
        /usr/lib/aarch64-linux-gnu/tegra-egl \
        > /etc/ld.so.conf.d/nvidia-tegra.conf \
    && mkdir -p /usr/lib/aarch64-linux-gnu/tegra /usr/lib/aarch64-linux-gnu/tegra-egl \
    && ldconfig

ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/tegra:/usr/lib/aarch64-linux-gnu/tegra-egl

# Inherited from the base, restated because housekeeping reclaims unlabelled images and this
# one is expensive to rebuild on a Nano.
LABEL io.ghspot.image=runner

USER runner
