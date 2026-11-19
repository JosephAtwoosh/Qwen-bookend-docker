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

| GPU | RAM | Profile | s/bookend | Notes |
|---|---|---|---|---|
| _not yet measured_ | | | | P2 of the plan: one rented instance, build, three images, terminate |

`./scripts/bench.sh 3` fills this in. Until it does, no throughput claim in this
repo should be treated as fact — the numbers above are file sizes, not timings.

## Weights on a rented box

57.8 GB per instance pull. Put `MODELS_HOST_DIR` on a persistent volume: rental
instances are ephemeral, and re-pulling 57.8 GB per session costs more time than
the generation itself. This is also why the repo exists at all — it is a *smaller*
weight set than LTX-2.5's ~76 GB, kept separate so neither job pays for the other.
