"""Frame-level video signals: motion, sharpness/blur, camera shake, brightness, black and exposure."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from autoedit_schemas.media import MediaInfo

from ..media import frame_spec, iter_frames

ANALYSIS_FPS = 10.0
ANALYSIS_WIDTH = 384

# Thresholds, calibrated on the eval clips. Recorded in signals.json under analysis.params.
PIXEL_CHANGE_LEVEL = 10  # 0..255 luma difference counted as "changed"
BLACK_LUMA = 0.07  # mean luma below this ...
BLACK_STD = 0.04  # ... with almost no texture is a black frame
UNDER_LUMA = 0.18
OVER_LUMA = 0.85
CLIP_LOW_SHARE = 0.45  # share of crushed pixels that also counts as underexposed
CLIP_HIGH_SHARE = 0.30
# Sharpness = 1 - var(Lap(gaussian(frame))) / var(Lap(frame)). Detail that a 1 px blur destroys is
# real detail, so the ratio is largely independent of scene content (unlike raw Laplacian variance).
# Measured: sharp 0.90-0.94, soft webcam 0.78-0.84, visibly blurred <= 0.70.
BLUR_ABS = 0.75  # sharpness below this is blurry regardless of the clip
BLUR_REL_DROP = 0.08  # ... or this far below the clip median (catches focus hunting / motion blur)
FLAT_STD = 0.03  # frames this flat (black, sky, wall) carry no blur information
SHAKE_MIN_RESPONSE = 0.8  # phase-correlation confidence needed to trust a global (camera) shift
SHAKE_THRESHOLD = 0.008  # RMS jitter as a share of the frame diagonal


@dataclass
class VideoFrames:
    """Per analysed frame (``ANALYSIS_FPS``) raw measurements."""

    times: npt.NDArray[np.float64]
    motion: npt.NDArray[np.float64]
    sharpness: npt.NDArray[np.float64]
    brightness: npt.NDArray[np.float64]
    black: npt.NDArray[np.bool_]
    under: npt.NDArray[np.bool_]
    over: npt.NDArray[np.bool_]
    shift: npt.NDArray[np.float64]  # (n, 2) global translation vs previous frame, pixels
    width: int
    diagonal: float
    params: dict[str, float | int | str | bool] = field(default_factory=dict)


def analyse_frames(proxy: Path, info: MediaInfo) -> VideoFrames:
    spec = frame_spec(info, ANALYSIS_FPS, ANALYSIS_WIDTH)
    times: list[float] = []
    motion: list[float] = []
    sharp: list[float] = []
    bright: list[float] = []
    black: list[bool] = []
    under: list[bool] = []
    over: list[bool] = []
    shifts: list[tuple[float, float]] = []

    window = cv2.createHanningWindow((spec.width, spec.height), cv2.CV_32F)
    prev_blur: npt.NDArray[Any] | None = None
    prev_f32: npt.NDArray[np.float32] | None = None
    n_pixels = spec.width * spec.height

    for t, gray in iter_frames(proxy, spec, gray=True):
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        f32 = gray.astype(np.float32)
        std = float(gray.std()) / 255.0
        if prev_blur is not None and prev_f32 is not None:
            diff = cv2.absdiff(blurred, prev_blur)
            motion.append(float(np.count_nonzero(diff > PIXEL_CHANGE_LEVEL)) / n_pixels)
            (dx, dy), resp = cv2.phaseCorrelate(prev_f32, f32, window)
            # Low confidence means the scene content moved, not the camera: no shake evidence.
            shifts.append((float(dx), float(dy)) if resp >= SHAKE_MIN_RESPONSE else (0.0, 0.0))
        else:
            motion.append(0.0)
            shifts.append((0.0, 0.0))

        mean = float(gray.mean()) / 255.0
        if std < FLAT_STD:
            sharp.append(float("nan"))
        else:
            lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            lap_soft = float(cv2.Laplacian(cv2.GaussianBlur(gray, (0, 0), 1.0), cv2.CV_64F).var())
            sharp.append(max(0.0, 1.0 - lap_soft / lap) if lap > 1e-6 else float("nan"))
        bright.append(mean)
        is_black = mean < BLACK_LUMA and std < BLACK_STD
        black.append(is_black)
        low_share = float(np.count_nonzero(gray < 16)) / n_pixels
        high_share = float(np.count_nonzero(gray > 245)) / n_pixels
        under.append(not is_black and (mean < UNDER_LUMA or low_share > CLIP_LOW_SHARE))
        over.append(mean > OVER_LUMA or high_share > CLIP_HIGH_SHARE)
        times.append(t)
        prev_blur, prev_f32 = blurred, f32

    if len(motion) > 1:
        motion[0] = motion[1]  # first frame has no predecessor
    return VideoFrames(
        times=np.asarray(times),
        motion=np.asarray(motion),
        sharpness=np.asarray(sharp),
        brightness=np.asarray(bright),
        black=np.asarray(black, dtype=bool),
        under=np.asarray(under, dtype=bool),
        over=np.asarray(over, dtype=bool),
        shift=np.asarray(shifts, dtype=np.float64).reshape(-1, 2),
        width=spec.width,
        diagonal=float(np.hypot(spec.width, spec.height)),
        params={
            "analysis_fps": ANALYSIS_FPS,
            "analysis_width": spec.width,
            "pixel_change_level": PIXEL_CHANGE_LEVEL,
            "blur_abs": BLUR_ABS,
            "blur_rel_drop": BLUR_REL_DROP,
            "shake_min_response": SHAKE_MIN_RESPONSE,
            "shake_threshold": SHAKE_THRESHOLD,
        },
    )


def shake_series(frames: VideoFrames) -> npt.NDArray[np.float64]:
    """Per-frame camera jitter: deviation of the camera path from its 1 s moving average.

    Intentional pans are smooth and survive the moving average, so they do not count as shake.
    """
    if frames.shift.shape[0] < 3:
        return np.zeros(frames.shift.shape[0])
    shift = frames.shift.copy()
    # Hard scene cuts produce huge bogus "shifts"; clamp them out.
    limit = frames.width * 0.15
    bad = np.linalg.norm(shift, axis=1) > limit
    shift[bad] = 0.0
    path = np.cumsum(shift, axis=0)
    k = max(3, int(ANALYSIS_FPS) | 1)  # odd window ~1 s
    kernel = np.ones(k) / k
    pad = k // 2
    smooth = np.column_stack(
        [np.convolve(np.pad(path[:, i], pad, mode="edge"), kernel, mode="valid") for i in range(2)]
    )
    residual = np.linalg.norm(path - smooth, axis=1)
    return np.asarray(residual / frames.diagonal, dtype=np.float64)


def blur_mask(sharpness: npt.NDArray[np.float64]) -> npt.NDArray[np.bool_]:
    """Blurry frames: absolutely soft, or clearly softer than the rest of the clip. NaN (flat) never."""
    valid = ~np.isnan(sharpness)
    if not valid.any():
        return np.zeros(sharpness.size, dtype=bool)
    median = float(np.median(sharpness[valid]))
    with np.errstate(invalid="ignore"):
        mask = (sharpness < BLUR_ABS) | (sharpness < median - BLUR_REL_DROP)
    return np.asarray(mask & valid, dtype=bool)
