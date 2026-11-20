# Hardware: what actually fits

All sizes below are read from the model repository's file listing
(`Qwen/Qwen-Image-Edit-2509`), not estimated from parameter counts.

| Component | bf16 on disk | fp8 (weight-only) |
|---|---|---|
| `transformer/` — 5 shards | **40.9 GB** | ~20.4 GB |
| `text_encoder/` — 4 shards (Qwen2.5-VL 7B) | **16.6 GB** | — |
| `vae/` | 0.25 GB | 0.25 GB |
| **total** | **~57.8 GB** | — |

## The planning assumption that had to be corrected

The original design discussion assumed the model "fits inside a single 24 GB 4500
Ada card in bfloat16". It does not: 40.9 GB of transformer cannot live in 24 GB.
The plan therefore is fp8 weight-only for the transformer (~20.4 GB, and Ada has
native fp8), with the 16.6 GB text encoder on the CPU.

## Two ceilings, not one

**VRAM** — fp8 transformer + VAE + activations at 1280×768 is ~22 GB. It fits a
24 GB card, with little room; `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`
is set in the image for that reason.

**RAM** — this is the one that surprises people:

| Moment | RAM |
|---|---|
| Converting bf16 → fp8 in place | ~43 GB peak (the whole bf16 transformer is resident first) |
| One loaded worker, steady state | ~37 GB (fp8 transformer + CPU text encoder) |
| Two loaded workers | ~74 GB |

So on a dual-4500-Ada box with 32–48 GB RAM, **the second GPU cannot be used** —
not because of VRAM, but because it would need its own text encoder. `WORKERS=0`
makes `doctor` and `batch` decide from measured RAM and say so; the warning is
explicit rather than a silent single-GPU run. Above ~74 GB RAM, two workers halve
wall-clock time.

## Profiles

| Profile | What it does | When |
|---|---|---|
| `fp8-t2i-cpu` | fp8 transformer on GPU, text encoder on CPU | default when RAM ≥ 48 GB and VRAM ≥ 21 GB |
| `fp8-offload` | fp8 + `enable_model_cpu_offload()` | tighter VRAM; slower |
| `bf16-offload` | `enable_sequential_cpu_offload()` | falls back to this automatically; slow, always works |
| `auto` | picks from measured RAM/VRAM | default in `.env.example` |

fp8 needs `torchao`. The image installs it with `--no-deps` so it cannot replace
the base image's CUDA 12.6 torch build. If `torchao` is unavailable or the
quantisation call fails, the engine says so and degrades to `bf16-offload` rather
than crashing mid-batch.

## Measured throughput

First real session, 2026-11-19/20, Vast.ai contract 61532948 (since destroyed):

| | |
|---|---|
| GPU | NVIDIA RTX 5000 Ada Generation, 32 760 MiB, driver 615.71.09 |
| Host | 503 GB RAM, 120 GB disk |
| Image | `ghcr.io/josephatwoosh/qwen-bookend-docker:latest`, 3.14 GiB, pulled in ~4 min |
| Weights | 57.7 GB fetched in **3 min 34 s** (~150 MB/s) from HF; ungated, no token |
| Model load | **26 s** warm; the 7B text encoder alone took several minutes cold |

| Profile | Used VRAM | Seconds per image (1280×768, 28 steps) |
|---|---|---|
| `fp8-dual` (both modules fp8, resident) | **30.9 GB peak** | **159 s** for a start, **229 s** for a stop |
| `bf16-offload` (sequential CPU offload) | 3.9 GB | **495 s** |

So a start/stop pair costs ~388 s, roughly $0.09 of GPU, and yields ~15 pairs an hour.
A whole 250-pair film is therefore **~27 GPU-hours (~$22)** — more than the $8 credit,
which is why the next session must first choose cheaper settings (see below).

The stop is ~44 % dearer than the start because it carries one more input image (the
rendered start), and VL encoding scales with the number of inputs.

## Profiles that do not work on this stack

Three paths were tried and are recorded so nobody repeats them:

1. **Text encoder pinned to the CPU, transformer on GPU.** Dies at generation with
   `Expected all tensors to be on the same device ... index_select`: the pipeline hands
   the CPU-resident prompt embeddings straight to the on-GPU transformer.
2. **fp8 + `enable_model_cpu_offload`.** Dies with
   `NotImplementedError: AffineQuantizedTensor dispatch ... _has_compatible_shallow_copy_type`,
   which torchao 0.9 does not implement. diffusers itself wants `torchao >= 0.15`
   (it disables its own integration below that), and 0.15+ wants torch ≥ 2.11, while
   this image is built on torch 2.6.0+cu126.
3. **`--profile auto` reaching the engine unresolved.** It fell through to sequential
   CPU offload — 495 s/image instead of 159 s. Fixed: `auto` is now resolved by
   `hardware.resolve_profile()` before the engine sees it, with tests.

## Settings to try next, in order

| Change | Expected | Risk |
|---|---|---|
| steps 28 → 20 | ~30 % faster | quality; check one frame first |
| 1280×768 → 1024×576 | ~40 % fewer tokens | bookends no longer match LTX's output size |
| both | pair ~170 s, film ~12 h (~$10) | compounding quality loss |

Also worth measuring: dropping the plate from *stop* tasks, since `start.jpg` already
carries the set. That removes one input from the more expensive half of every pair.


## Weights on a rented box

57.8 GB per instance pull. Put `MODELS_HOST_DIR` on a persistent volume: rental
instances are ephemeral, and re-pulling 57.8 GB per session costs more time than
the generation itself. This is also why the repo exists at all — it is a *smaller*
weight set than LTX-2.5's ~76 GB, kept separate so neither job pays for the other.
