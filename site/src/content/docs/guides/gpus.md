---
title: "GPUs"
description: "Giving a pool a GPU, and what the host needs first."
---

A pool can hand its jobs the host's GPUs:

```toml
[[pool]]
name = "gpu"
repository = "you/your-project"
labels = ["self-hosted", "linux", "x64", "gpu-a100"]

# Without this, a job asking only for [self-hosted, linux, x64] lands here and burns
# the GPU on work that never wanted one. See "Stop the GPU taking CPU work" below.
requires_labels = ["gpu-a100"]

max_runners = 1

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
gpus = "all"          # or a count: 1  —  or specific ids: ["0", "1"]
```

`gpus` is the same selection `docker run --gpus` takes. Device ids are as `nvidia-smi -L`
numbers them.

## The host needs the NVIDIA Container Toolkit

Drivers alone are not enough — the Engine needs the toolkit to pass a device through:

```bash
# Ubuntu / Debian
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit

sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Check it before configuring a pool:

```bash
docker run --rm --gpus all ubuntu:24.04 nvidia-smi
```

`ghspot doctor` checks for the toolkit whenever a pool asks for GPUs, because without it
**every runner in that pool fails to start** — with an error about device requests that says
nothing about a missing toolkit.

## Stop the GPU taking CPU work

Label matching is a **subset** rule: a runner serves a job when it carries every label the
job asked for. Extra labels on the runner are ignored. So a pool labelled
`self-hosted, linux, x64, gpu-a100` will accept a job asking for only
`self-hosted, linux, x64`, and the GPU then runs work that never needed one.

`requires_labels` inverts the rule for the labels you name: the job must have asked for them.

```toml
labels = ["self-hosted", "linux", "x64", "gpu-a100"]
requires_labels = ["gpu-a100"]
```

Now the pool serves this:

```yaml
runs-on: [self-hosted, gpu-a100]
```

and refuses a plain `runs-on: [self-hosted, linux, x64]`.

**Name the hardware, not the category.** `gpu-a100` or `gpu-2080ti` rather than `gpu`, so a
workflow that needs 24 GB of VRAM cannot land on a card with 8. The label is the only thing a
workflow author can see.

Set `max_runners` to the number of GPUs you actually have. Two runners sharing one card both
run, and both are slower than either alone.

## What this does and does not prevent

`requires_labels` governs **which pool the daemon scales up for**, and that is the part that
wastes a GPU: without it, a queue of CPU jobs makes the daemon start GPU runners to serve
them.

It does not govern which runner GitHub hands a job to. GitHub also applies its own labels to
every self-hosted runner — `self-hosted`, the OS, the architecture — and those cannot be
removed. So a GPU runner that is *already up and idle* can still be handed a plain CPU job
by GitHub before it is reaped.

Two things shrink that window to almost nothing:

- **`min_idle = 0` on GPU pools.** A GPU runner then exists only while there is GPU work for
  it, rather than sitting idle waiting to be given something else.
- **A distinguishing label on your CPU pools too**, and workflows that ask for it. If nothing
  in your repository says `runs-on: [self-hosted, linux, x64]`, nothing can drift onto the
  GPU box in the first place.

## What the image does and does not carry

The toolkit injects the driver libraries, so `nvidia-smi` and anything CUDA-runtime works
inside a job without the image carrying drivers.

It does **not** provide the CUDA toolkit — there is no `nvcc`. Compiling CUDA means either a
container built for it, or a `setup-` action that fetches one. Baking CUDA into the runner
image would add several gigabytes to every variant for the sake of a minority of jobs.
