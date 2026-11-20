"""Review: mechanical checks on each start/stop pair, then a pack small enough to pull.

Rendering 40 pairs quickly is worthless if reviewing them takes an hour. So every
pair gets three cheap measurements -- SSIM, mean absolute difference, hue-histogram
distance -- and a verdict. The human then looks at the exceptions instead of at
everything.

The two failure modes being caught are the ones that cost renders in take 2:

  stalled   start and stop are nearly the same picture: there is nothing for
            FLF2V to interpolate, so the shot will look frozen or drift on its own

  drift     the pair disagrees about lighting, background or framing, so the video
            model has to invent a path between two different worlds
"""
from __future__ import annotations

import csv
import html
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .config import Config
from .shot import Shot

DEFAULT_THUMB_WIDTH = 480
SSIM_HIGH = 0.97   # above this the pair is effectively the same frame
SSIM_LOW = 0.30    # below this the subject itself probably jumped, whatever the palette did
SSIM_MOVE = 0.60   # 0.30-0.60 with the palette held is a camera move, which is a pass
HUE_HIGH = 0.20    # L1 distance of normalised hue histograms; the primary drift signal
WORK_WIDTH = 256   # metrics are computed at this width for speed and scale-invariance


@dataclass
class PairResult:
    shot: str
    start: Path | None
    stop: Path | None
    ssim: float | None = None
    mean_abs_diff: float | None = None
    hue_distance: float | None = None
    verdict: str = "missing"
    reasons: list[str] = field(default_factory=list)
    seed_start: int | None = None
    seed_stop: int | None = None
    seconds: float | None = None

    def to_row(self) -> dict:
        return {
            "shot": self.shot,
            "verdict": self.verdict,
            "ssim": "" if self.ssim is None else f"{self.ssim:.4f}",
            "mean_abs_diff": "" if self.mean_abs_diff is None else f"{self.mean_abs_diff:.2f}",
            "hue_distance": "" if self.hue_distance is None else f"{self.hue_distance:.4f}",
            "start": "" if self.start is None else self.start.name,
            "stop": "" if self.stop is None else self.stop.name,
            "reasons": "; ".join(self.reasons),
        }


def _load_gray(path: Path, width: int = WORK_WIDTH) -> np.ndarray:
    with Image.open(path) as im:
        im = im.convert("L")
        ratio = width / im.width
        im = im.resize((width, max(1, int(round(im.height * ratio)))), Image.BILINEAR)
        return np.asarray(im, dtype=np.float64)


def _box_mean(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window, edge-padded, via an integral image."""
    k = 2 * r + 1
    ap = np.pad(a, r, mode="edge")
    ii = np.zeros((ap.shape[0] + 1, ap.shape[1] + 1), dtype=np.float64)
    ii[1:, 1:] = ap.cumsum(0).cumsum(1)
    h, w = a.shape
    ys = np.arange(h)
    xs = np.arange(w)
    total = ii[ys + k][:, xs + k] - ii[ys][:, xs + k] - ii[ys + k][:, xs] + ii[ys][:, xs]
    return total / (k * k)


def ssim(a: np.ndarray, b: np.ndarray, r: int = 5) -> float:
    """SSIM with a box window (not Gaussian), reported as an approximation.

    Comparable between pairs on the same setup, which is all the verdict needs;
    it is not meant to reconcile with skimage to the third decimal.
    """
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a, b = a[:h, :w], b[:h, :w]
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    mu_a, mu_b = _box_mean(a, r), _box_mean(b, r)
    va = _box_mean(a * a, r) - mu_a**2
    vb = _box_mean(b * b, r) - mu_b**2
    cab = _box_mean(a * b, r) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * cab + c2)
    den = (mu_a**2 + mu_b**2 + c1) * (va + vb + c2)
    return float(np.mean(num / den))


def hue_histogram(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        hsv = im.convert("HSV").resize((WORK_WIDTH, WORK_WIDTH), Image.BILINEAR)
        hist = np.asarray(hsv.getchannel("H").histogram(), dtype=np.float64)
    total = hist.sum() or 1.0
    return hist / total


def hue_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Half the L1 distance of two normalised histograms: 0 identical, 1 disjoint."""
    return float(0.5 * np.abs(a - b).sum())


def judge(
    pair: PairResult, *, ssim_high: float = SSIM_HIGH, ssim_low: float = SSIM_LOW, hue_high: float = HUE_HIGH, ssim_move: float = SSIM_MOVE
) -> PairResult:
    if pair.start is None or pair.stop is None:
        pair.verdict = "missing"
        return pair
    if pair.ssim is None:
        pair.verdict = "unreadable"
        return pair

    # Structural reasons ("could not read image") stay; verdict reasons are rebuilt
    # so re-judging with different thresholds cannot duplicate them.
    pair.reasons = [r for r in pair.reasons if not _is_verdict_reason(r)]

    if pair.ssim >= ssim_high:
        pair.verdict = "stalled"
        pair.reasons.append(
            f"SSIM {pair.ssim:.3f} >= {ssim_high}: start and stop are nearly the same frame, "
            f"so there is almost no motion to interpolate"
        )
    elif pair.hue_distance is not None and pair.hue_distance > hue_high:
        # Colour moved: that is the failure FLF2V cannot hide, whatever the SSIM says.
        pair.verdict = "drift"
        pair.reasons.append(
            f"hue distance {pair.hue_distance:.3f} > {hue_high}: the colour balance moved between "
            f"frames, which reads as a different time of day"
        )
    elif pair.ssim <= ssim_low:
        pair.verdict = "drift"
        pair.reasons.append(
            f"SSIM {pair.ssim:.3f} <= {ssim_low}: the pair has little in common even though the "
            f"palette held; check that the subject did not jump"
        )
    elif pair.ssim <= ssim_move:
        # Recalibrated 2026-11-20 on 16 hosted pairs: cinematic beats (a push in, the sun
        # rising, a subject stepping forward) land at SSIM 0.35-0.60 with hue under 0.12.
        # Calling those "drift" flagged 10 of 16 good pairs. A camera move is a pass.
        pair.verdict = "pass"
        pair.reasons.append(
            f"SSIM {pair.ssim:.3f}: deliberate camera move (hue held at "
            f"{pair.hue_distance if pair.hue_distance is not None else 0:.3f})"
        )
    else:
        pair.verdict = "pass"
    return pair


def measure(
    shot: Shot,
    *,
    ssim_high: float = SSIM_HIGH,
    ssim_low: float = SSIM_LOW,
    hue_high: float = HUE_HIGH,
    ssim_move: float = SSIM_MOVE,
) -> PairResult:
    start = shot.root / "start.jpg"
    stop = shot.root / "stop.jpg"
    pair = PairResult(
        shot=shot.name,
        start=start if start.is_file() else None,
        stop=stop if stop.is_file() else None,
    )
    if pair.start is None or pair.stop is None:
        missing = [n for n, p in (("start.jpg", pair.start), ("stop.jpg", pair.stop)) if p is None]
        pair.reasons.append(f"missing {', '.join(missing)}")
        return pair

    try:
        a = _load_gray(pair.start)
        b = _load_gray(pair.stop)
        pair.ssim = ssim(a, b)
        pair.mean_abs_diff = float(np.mean(np.abs(a - b)))
        pair.hue_distance = hue_distance(hue_histogram(pair.start), hue_histogram(pair.stop))
    except OSError as exc:
        pair.reasons.append(f"could not read image: {exc}")
        return pair

    for tag in ("start", "stop"):
        sidecar = shot.root / f"{tag}.json"
        if sidecar.is_file():
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if tag == "start":
                pair.seed_start = data.get("seed")
            else:
                pair.seed_stop = data.get("seed")
            if data.get("seconds") and pair.seconds is None:
                pair.seconds = data["seconds"]
    return judge(pair, ssim_high=ssim_high, ssim_low=ssim_low, hue_high=hue_high)


_VERDICT_REASON_PREFIXES = ("SSIM ", "hue distance ")


def _is_verdict_reason(reason: str) -> bool:
    return reason.startswith(_VERDICT_REASON_PREFIXES)


def _thumb(src: Path, dest: Path, width: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        ratio = width / im.width
        im = im.resize((width, max(1, int(round(im.height * ratio)))), Image.LANCZOS)
        im.save(dest, format="JPEG", quality=82)


BADGE = {
    "pass": ("#1f7a3f", "pass"),
    "stalled": ("#8a6d00", "stalled"),
    "drift": ("#8a2020", "drift"),
    "missing": ("#5a5a5a", "missing"),
    "unreadable": ("#8a2020", "unreadable"),
}


def _prompt_for(shot_root: Path, tag: str) -> str:
    sidecar = shot_root / f"{tag}.json"
    if not sidecar.is_file():
        return ""
    try:
        return json.loads(sidecar.read_text(encoding="utf-8")).get("prompt", "")
    except (OSError, json.JSONDecodeError):
        return ""


def build(
    cfg: Config,
    shots: list[Shot],
    *,
    out_dir: Path | None = None,
    thumb_width: int = DEFAULT_THUMB_WIDTH,
    ssim_high: float = SSIM_HIGH,
    ssim_low: float = SSIM_LOW,
) -> dict:
    out_dir = Path(out_dir) if out_dir else Path(cfg.output_dir)
    review_dir = out_dir / "review"
    thumbs = review_dir / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)

    results = [
        measure(s, ssim_high=ssim_high, ssim_low=ssim_low) for s in shots
    ]

    for r in results:
        if r.start is not None:
            _thumb(r.start, thumbs / f"{r.shot}_start.jpg", thumb_width)
        if r.stop is not None:
            _thumb(r.stop, thumbs / f"{r.shot}_stop.jpg", thumb_width)

    _write_csv(results, review_dir / "metrics.csv")
    _write_html(results, shots, review_dir, thumb_width)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    summary = {
        "review_dir": str(review_dir),
        "shots": len(results),
        "verdicts": counts,
        "flagged": sum(v for k, v in counts.items() if k != "pass"),
        "pairs": [r.to_row() for r in results],
    }
    (review_dir / "review.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _write_csv(results: list[PairResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["shot", "verdict", "ssim", "mean_abs_diff", "hue_distance", "start", "stop", "reasons"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in results:
            writer.writerow(r.to_row())


def _write_html(results: list[PairResult], shots: list[Shot], review_dir: Path, thumb_width: int) -> None:
    by_name = {s.name: s for s in shots}
    rows = []
    for r in results:
        colour, label = BADGE.get(r.verdict, ("#5a5a5a", r.verdict))
        shot = by_name.get(r.shot)
        prompt_start = html.escape(_prompt_for(shot.root, "start")) if shot else ""
        prompt_stop = html.escape(_prompt_for(shot.root, "stop")) if shot else ""
        start_src = f"thumbs/{r.shot}_start.jpg" if r.start else ""
        stop_src = f"thumbs/{r.shot}_stop.jpg" if r.stop else ""
        cells = []
        for src, tag in ((start_src, "start"), (stop_src, "stop")):
            cells.append(
                f'<td><img src="{src}" alt="{html.escape(r.shot)} {tag}" '
                f'style="width:{thumb_width}px"></td>'
                if src
                else '<td class="empty">missing</td>'
            )
        metric_bits = []
        if r.ssim is not None:
            metric_bits.append(f"SSIM {r.ssim:.3f}")
        if r.mean_abs_diff is not None:
            metric_bits.append(f"MAD {r.mean_abs_diff:.1f}")
        if r.hue_distance is not None:
            metric_bits.append(f"hue {r.hue_distance:.3f}")
        if r.seconds is not None:
            metric_bits.append(f"{r.seconds:.1f}s")
        reason_html = "".join(f"<li>{html.escape(x)}</li>" for x in r.reasons)
        rows.append(
            f"""<tr>
  <td class="shot"><b>{html.escape(r.shot)}</b><br>
    <span class="badge" style="background:{colour}">{html.escape(label)}</span><br>
    <span class="metric">{html.escape(" &middot; ".join(metric_bits))}</span>
  </td>
  {cells[0]}{cells[1]}
  <td class="detail">
    {f'<ul class="reasons">{reason_html}</ul>' if r.reasons else ''}
    <details><summary>prompts</summary>
      <div class="prompt"><b>start</b> {prompt_start or "<i>no sidecar</i>"}</div>
      <div class="prompt"><b>stop</b> {prompt_stop or "<i>no sidecar</i>"}</div>
    </details>
  </td>
</tr>"""
        )

    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    headline = " &middot; ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing to review"

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Bookend review</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; margin: 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #777; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td {{ vertical-align: top; padding: 10px; border-top: 1px solid #ddd; }}
  img {{ border-radius: 6px; display: block; }}
  .shot {{ width: 150px; }}
  .badge {{ display: inline-block; margin-top: 6px; padding: 1px 8px; border-radius: 10px;
            color: #fff; font-size: 12px; font-weight: 600; }}
  .metric {{ display: inline-block; margin-top: 6px; color: #666; font-size: 12px; }}
  .detail {{ max-width: 420px; }}
  .reasons {{ margin: 0 0 8px; padding-left: 18px; color: #8a2020; }}
  .prompt {{ margin: 6px 0; font-size: 12px; color: #444; }}
  .empty {{ color: #999; font-style: italic; }}
  summary {{ cursor: pointer; color: #555; font-size: 12px; }}
</style></head>
<body>
<h1>Bookend review</h1>
<div class="sub">{headline}</div>
<table>
<tbody>
{"".join(rows)}
</tbody></table>
</body></html>
"""
    (review_dir / "index.html").write_text(doc, encoding="utf-8")
