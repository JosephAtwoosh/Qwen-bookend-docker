# Qwen-Image-Edit-2509 bookend generator.
#
# Base image: pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime -- the tag was checked
# on Docker Hub (3.27 GB compressed), not assumed.
#
# This image deliberately does NOT install natten or the LTX stack: bookends are a
# separate job with separate weights, which is the whole point of the split. The
# only shared thing with ltx25-docker is the shot tarball.
FROM pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime

ARG DIFFUSERS_REF=main
ARG TORCHAO_SPEC=torchao>=0.8

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/models/hf_cache \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# QwenImageEditPlusPipeline (multi-image editing) lives in diffusers; the model card
# says install from git. Pin with --build-arg DIFFUSERS_REF=<sha> once a working
# revision is known -- "main" is a moving target and this is recorded as a risk.
#
# transformers has no upper bound on purpose: diffusers@main tracks a recent
# transformers, and capping it at <5 made the resolver give up (ResolutionImpossible).
# Let diffusers choose, then record what it chose so the image is explainable.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      "diffusers @ git+https://github.com/huggingface/diffusers@${DIFFUSERS_REF}" \
      "transformers>=4.49" \
      "accelerate>=1.0" \
      "safetensors>=0.4" \
      "pillow>=10" \
      "numpy>=1.24" \
 && python -c "import diffusers, transformers, torch; print('resolved', 'diffusers', diffusers.__version__, '| transformers', transformers.__version__, '| torch', torch.__version__)" | tee /usr/local/share/bookends-versions.txt

# torchao provides float8 weight-only quantisation. --no-deps is deliberate: torchao
# depends on torch, and letting pip resolve it would replace the CUDA 12.6 build
# this image is based on with whatever wheel pip finds first.
RUN pip install --no-cache-dir --no-deps "${TORCHAO_SPEC}"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY bookends ./bookends
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps -e . \
 && chmod +x /app/scripts/*.sh

# Weights are downloaded at runtime into the mounted volume, never baked in: a
# 58 GB layer would make the image useless to pull.
ENV SHOTS_DIR=/shots \
    REFERENCE_DIR=/reference \
    OUTPUT_DIR=/output \
    MODELS_DIR=/models

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["doctor"]
