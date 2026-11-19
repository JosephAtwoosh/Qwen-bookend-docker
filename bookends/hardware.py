"""Hardware inspection, so the profile and worker count are decided by facts.

The sizes that matter, measured from the model repo rather than assumed:

    transformer   40.9 GB  (bf16, 5 shards)   -> 20.4 GB at fp8
    text encoder  16.6 GB  (Qwen2.5-VL 7B)    -> stays on CPU
    VAE            0.25 GB

So a worker needs ~22 GB VRAM but ~37 GB of system RAM once loaded, and two
workers need ~74 GB. That makes the second GPU a RAM question, not a VRAM one --
which is the opposite of what the planning thread assumed.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import ctypes
from pathlib import Path

# Measured footprint of Qwen-Image-Edit-2509, in GB.
TRANSFORMER_BF16_GB = 40.9
TEXT_ENCODER_BF16_GB = 16.6
VAE_GB = 0.25
FP8_FACTOR = 0.5

# Peak RAM while converting bf16 -> fp8 in place (all bf16 resident first).
FP8_CONVERSION_PEAK_GB = TRANSFORMER_BF16_GB + 2
# Steady state of one loaded worker: fp8 transformer + CPU text encoder + slack.
PER_WORKER_STEADY_GB = TRANSFORMER_BF16_GB * FP8_FACTOR + TEXT_ENCODER_BF16_GB + 3


def gpus() -> list[dict]:
    """Parse nvidia-smi. Empty list when there is no CUDA device."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    devices = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            devices.append({"index": int(parts[0]), "name": parts[1], "vram_gb": round(int(parts[2]) / 1024, 1)})
        except ValueError:
            continue
    return devices


def ram_gb() -> float | None:
    """Total system RAM in GB, or None if it cannot be determined."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / 1024 / 1024, 1)
        except OSError:
            return None
        return None
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        try:
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / 1024**3, 1)
        except (OSError, AttributeError):
            return None
    return None


def free_ram_gb() -> float | None:
    if sys.platform.startswith("linux"):
        try:
            values = {}
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    values[key] = int(rest.split()[0])
            avail = values.get("MemAvailable")
            return round(avail / 1024 / 1024, 1) if avail else None
        except (OSError, ValueError, IndexError):
            return None
    return None


def model_cache_size_gb(models_dir: Path) -> float:
    root = Path(models_dir)
    if not root.exists():
        return 0.0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return round(total / 1024**3, 1)


def recommend(cfg) -> dict:
    """Decide profile and worker count from measured hardware.

    Returns a dict with the decision plus the reasons, so `doctor` can print it
    and an operator can disagree with it on purpose.
    """
    devices = gpus()
    ram = ram_gb()
    vram = min((d["vram_gb"] for d in devices), default=0.0)
    reasons: list[str] = []
    warnings: list[str] = []

    profile = cfg.profile
    if profile == "auto":
        if not devices:
            profile = "bf16-offload"
            reasons.append("no CUDA device visible: pregenerated weights/stills only")
        elif ram is None:
            profile = "fp8-t2i-cpu"
            reasons.append("RAM unknown; assuming fp8 conversion fits")
        elif ram >= 48 and vram >= 21:
            profile = "fp8-t2i-cpu"
            reasons.append(f"{ram:.0f} GB RAM and {vram:.0f} GB VRAM fit fp8 + CPU text encoder")
        else:
            profile = "bf16-offload"
            reasons.append(
                f"{ram:.0f} GB RAM is below the ~48 GB needed to convert to fp8 in place; "
                f"using sequential CPU offload"
            )

    if profile in ("fp8-t2i-cpu", "fp8-offload") and ram is not None and ram < FP8_CONVERSION_PEAK_GB:
        warnings.append(
            f"fp8 conversion holds the full {TRANSFORMER_BF16_GB:.0f} GB bf16 transformer in RAM "
            f"first; {ram:.0f} GB is under the ~{FP8_CONVERSION_PEAK_GB:.0f} GB needed"
        )
    if profile == "fp8-t2i-cpu" and vram and vram < 21:
        warnings.append(
            f"{vram:.0f} GB VRAM per card is tight for the ~20.4 GB fp8 transformer; "
            f"prefer fp8-offload on this box"
        )

    workers = cfg.workers
    if workers == 0:
        if not devices:
            workers = 1
        elif ram is None:
            workers = 1
            reasons.append("RAM unknown, so one worker only")
        else:
            fit = int(ram // PER_WORKER_STEADY_GB)
            workers = max(1, min(len(devices), fit))
            reasons.append(
                f"{ram:.0f} GB RAM / {PER_WORKER_STEADY_GB:.0f} GB per worker -> {workers} worker(s) "
                f"across {len(devices)} GPU(s)"
            )
            if workers < len(devices):
                warnings.append(
                    f"{len(devices)} GPUs but only {workers} worker(s): the second card would need "
                    f"another ~{PER_WORKER_STEADY_GB:.0f} GB of RAM for its own text encoder"
                )

    return {
        "profile": profile,
        "workers": workers,
        "gpus": devices,
        "ram_gb": ram,
        "free_ram_gb": free_ram_gb(),
        "vram_gb": vram,
        "reasons": reasons,
        "warnings": warnings,
    }
