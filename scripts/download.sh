#!/usr/bin/env bash
# Pre-cache Qwen-Image-Edit-2509 into the mounted models volume.
#
# Sizes (from the HF file listing): transformer 40.9 GB + text encoder 16.6 GB
# + VAE 0.25 GB ~= 57.8 GB. On Vast.ai, put ./models on a persistent volume so
# this is paid once per project rather than once per rented instance.
set -euo pipefail

python3 - <<'PY'
import os

from huggingface_hub import snapshot_download

model = os.environ.get("MODEL_ID", "Qwen/Qwen-Image-Edit-2509")
revision = os.environ.get("MODEL_REVISION") or None
home = os.environ.get("HF_HOME", "(default)")

print(f"model    {model}")
print(f"revision {revision or 'main'}")
print(f"hf_home  {home}")
print("downloading (resumable)...")
path = snapshot_download(repo_id=model, revision=revision)
print(f"cached at {path}")
PY
