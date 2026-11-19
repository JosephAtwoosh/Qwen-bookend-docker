"""Bookend (start/stop frame) generation for LTX-2.5 first-last-frame-to-video.

The package is deliberately small and dependency-light at import time: `torch`
and `diffusers` are imported only inside `bookends.pipe`, so the whole pipeline
can be exercised -- prep, dry-run, review metrics, packing -- on a machine with
no GPU and no CUDA wheels installed.
"""

__version__ = "0.1.0"
