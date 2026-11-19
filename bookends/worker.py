"""Worker loop: render tasks, one engine per GPU, skip what already exists.

Parallelism is process-based, one process per GPU. There is no tensor
parallelism: dual RTX 4500 Ada has no NVLink, so splitting one model across both
cards is slower than running two independent bookends. The catch is RAM, not
VRAM -- each worker wants its own 16.6 GB text encoder -- which is why the
worker count comes from `hardware.recommend()` rather than from `torch.cuda.device_count()`.
"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from PIL import Image

from .config import Config
from .pipe import Engine, make_engine, sidecar_payload


def write_output(cfg: Config, task, image: Image.Image) -> Path:
    task.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(task.out, format="JPEG", quality=cfg.jpeg_quality, subsampling=0)
    return task.out


def render_one(cfg: Config, task, engine: Engine) -> dict:
    started = time.perf_counter()
    image = engine.generate(task)
    seconds = time.perf_counter() - started
    write_output(cfg, task, image)
    payload = sidecar_payload(cfg, task, engine, seconds, image)
    sidecar = task.out.with_suffix(".json")
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "key": task.key,
        "out": str(task.out),
        "seconds": round(seconds, 2),
        "ok": True,
        "seed": task.seed,
    }


def render(tasks: list, cfg: Config, *, workers: int = 1, fake: bool = False, log=print) -> list[dict]:
    """Render every task and return a per-task result list (failures included)."""
    if not tasks:
        return []

    if fake or workers <= 1:
        engine = make_engine(cfg, 0, fake=fake)
        engine.start()
        results = []
        try:
            for task in tasks:
                results.append(_guarded(cfg, task, engine, log))
        finally:
            engine.close()
        return results

    # One process per GPU. Partition round-robin so both cards stay busy.
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    buckets = [(i, tasks[i::workers]) for i in range(workers) if tasks[i::workers]]
    procs = [
        ctx.Process(target=_worker, args=(gpu_id, bucket, cfg, queue), daemon=False)
        for gpu_id, bucket in buckets
    ]
    for p in procs:
        p.start()
    results: list[dict] = []
    expected = sum(len(b) for _, b in buckets)
    while len(results) < expected:
        try:
            item = queue.get(timeout=600)
        except Exception:  # pragma: no cover - a worker died silently
            if not any(p.is_alive() for p in procs):
                break
            continue
        if item is None:
            continue
        results.append(item)
        if item.get("ok"):
            log(
                f"  ok   {item['key']:<28} {item['seconds']:6.1f}s  seed {item['seed']}"
            )
        else:
            log(f"  FAIL {item['key']:<28} {item.get('error', '')[:160]}")
    for p in procs:
        p.join()
    return results


def _worker(gpu_id: int, tasks: list, cfg: Config, queue) -> None:
    try:
        engine = make_engine(cfg, gpu_id, fake=False)
        engine.start()
    except Exception as exc:  # pragma: no cover - startup failure
        queue.put(
            {
                "key": f"gpu{gpu_id}:startup",
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        return
    try:
        for task in tasks:
            queue.put(_guarded(cfg, task, engine, lambda *a, **k: None))
    finally:
        engine.close()


def _guarded(cfg: Config, task, engine: Engine, log) -> dict:
    try:
        return render_one(cfg, task, engine)
    except Exception as exc:  # a bad shot must not kill the batch
        log(f"  FAIL {task.key}: {type(exc).__name__}: {exc}")
        return {
            "key": task.key,
            "out": str(task.out),
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


def batch_manifest(
    cfg: Config, tasks: list, results: list[dict], *, hw: dict | None = None, style: str | None = None
) -> dict:
    ok = [r for r in results if r.get("ok")]
    return {
        "schema": 1,
        "model": {"id": cfg.model_id, "revision": cfg.revision},
        "profile": (hw or {}).get("profile", cfg.profile),
        "style": style or cfg.style,
        "pair_mode": cfg.pair_mode,
        "size": [cfg.width, cfg.height],
        "sampling": {
            "steps": cfg.steps,
            "true_cfg_scale": cfg.true_cfg_scale,
            "guidance_scale": cfg.guidance_scale,
            "seed_base": cfg.seed_base,
        },
        "hardware": hw or {},
        "rendered": len(ok),
        "failed": len(results) - len(ok),
        "items": results,
    }
