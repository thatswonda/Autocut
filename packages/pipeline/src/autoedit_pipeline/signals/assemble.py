"""Fuse the raw video, subject, scene and audio measurements into a validated ``Signals`` document."""

from __future__ import annotations

import itertools
import math

import numpy as np
import numpy.typing as npt

from autoedit_schemas.media import MediaInfo
from autoedit_schemas.signals import (
    AnalysisInfo,
    AudioSummary,
    Box,
    FlaggedRange,
    Point,
    Range,
    Scene,
    SecondMetrics,
    Signals,
    Summary,
    VideoSummary,
)

from .audio import VAD_FRAME_S, AudioAnalysis
from .ranges import mask_to_ranges, ranges_overlap_seconds
from .subjects import SubjectSample, SubjectTrack
from .video import ANALYSIS_FPS, SHAKE_THRESHOLD, VideoFrames, blur_mask, shake_series

DEAD_MOTION = 0.003  # share of changed pixels per frame (1 s average) below which a shot is static
DEAD_SPEECH_PAD_S = 0.4  # keep this much breathing room around speech out of dead zones
MIN_DEAD_S = 1.5
MIN_SUBJECT_HOLD_S = 3.0  # a visible person must hold still this long before we call it dead
MIN_SUBJECT_FACE_AREA = 0.004  # faces smaller than this share of the frame do not count as a subject
MIN_FLAG_S = {"blur": 0.6, "shake": 0.6, "black": 0.2, "underexposed": 1.0, "overexposed": 1.0}


def _r(x: float, nd: int = 4) -> float:
    return float(round(x, nd))


def _moving_average(x: npt.NDArray[np.float64], k: int) -> npt.NDArray[np.float64]:
    if x.size == 0 or k <= 1:
        return x
    k = min(k, x.size)
    pad = k // 2
    padded = np.pad(x, (pad, k - 1 - pad), mode="edge")
    return np.convolve(padded, np.ones(k) / k, mode="valid")


def _nanmedian(x: npt.NDArray[np.float64]) -> float:
    valid = x[~np.isnan(x)]
    return float(np.median(valid)) if valid.size else 0.0


def _majority(mask: npt.NDArray[np.bool_], idx: npt.NDArray[np.int64], s: int) -> bool:
    sel = mask[idx == s]
    return bool(sel.size and sel.mean() > 0.5)


def _speech_at(
    audio: AudioAnalysis | None, times: npt.NDArray[np.float64], pad_s: float
) -> npt.NDArray[np.bool_]:
    """Whether speech occurs within ``pad_s`` of each time."""
    out = np.zeros(times.size, dtype=bool)
    if audio is None:
        return out
    for a, b in audio.speech_ranges:
        out |= (times >= a - pad_s) & (times <= b + pad_s)
    return out


def _subject_presence(track: SubjectTrack, times: npt.NDArray[np.float64]) -> npt.NDArray[np.bool_]:
    """Whether a meaningful subject (a person, or a face of usable size) is visible at each time."""
    if not track.samples or times.size == 0:
        return np.zeros(times.size, dtype=bool)
    st = np.array([s.t for s in track.samples])
    present = np.array(
        [bool(s.bodies) or any(f.area >= MIN_SUBJECT_FACE_AREA for f in s.faces) for s in track.samples]
    )
    idx = np.clip(np.searchsorted(st, times, side="right") - 1, 0, st.size - 1)
    return np.asarray(present[idx], dtype=bool)


def _best_sample(samples: list[SubjectSample]) -> SubjectSample | None:
    if not samples:
        return None
    return max(samples, key=lambda s: (len(s.faces) > 0, len(s.bodies), sum(f.score for f in s.faces)))


def assemble(
    *,
    clip_id: str,
    sha256: str,
    info: MediaInfo,
    frames: VideoFrames,
    subjects: SubjectTrack,
    scene_cuts: list[float],
    audio: AudioAnalysis | None,
    timings: dict[str, float],
    pipeline_version: str,
) -> Signals:
    duration = info.duration_s
    n = max(1, math.ceil(duration - 1e-6))
    ft = frames.times
    fidx = np.clip(np.floor(ft).astype(np.int64), 0, n - 1)

    shake = shake_series(frames)
    blurry = blur_mask(frames.sharpness)
    shaky = shake > SHAKE_THRESHOLD
    motion_smooth = _moving_average(frames.motion, int(ANALYSIS_FPS))

    # ---- dead zones at analysis-frame resolution ---------------------------------------------------
    # Dead = black, or static with no speech nearby. Static shots with a visible person are graded
    # separately ("static_subject_hold") so Stage C samples them sparsely instead of skipping them.
    speech_near = _speech_at(audio, ft, DEAD_SPEECH_PAD_S)
    static = (motion_smooth < DEAD_MOTION) & ~speech_near
    subject_at = _subject_presence(subjects, ft)
    step = 1.0 / ANALYSIS_FPS
    candidates = mask_to_ranges(
        static | frames.black, step, min_len_s=MIN_DEAD_S, merge_gap_s=0.5, end_s=duration
    )
    silences = audio.silences if audio else []
    dead_zones: list[FlaggedRange] = []
    for a, b in candidates:
        sel = (ft >= a) & (ft < b)
        black_share = float(frames.black[sel].mean()) if sel.any() else 0.0
        subject_share = float(subject_at[sel].mean()) if sel.any() else 0.0
        if black_share > 0.5:
            reason = "black_frames"
        elif subject_share > 0.5:
            if b - a < MIN_SUBJECT_HOLD_S:
                continue
            reason = "static_subject_hold"
        else:
            reason = "static_empty"
        dead_zones.append(FlaggedRange(t_in=a, t_out=b, reason=reason))
    dead_ranges = [(z.t_in, z.t_out) for z in dead_zones]
    dead_cover = ranges_overlap_seconds(dead_ranges, n)

    # ---- quality flags -----------------------------------------------------------------------------
    flags: list[FlaggedRange] = []
    for name, mask in (
        ("blur", blurry & ~frames.black),
        ("shake", shaky),
        ("black", frames.black),
        ("underexposed", frames.under),
        ("overexposed", frames.over),
    ):
        for a, b in mask_to_ranges(mask, step, min_len_s=MIN_FLAG_S[name], merge_gap_s=0.3, end_s=duration):
            flags.append(FlaggedRange(t_in=a, t_out=b, reason=name))
    flags.sort(key=lambda f: (f.t_in, f.reason))

    # ---- scenes ------------------------------------------------------------------------------------
    cuts = sorted({c for c in scene_cuts if 0.0 < c < duration})
    bounds = [0.0, *cuts, duration]
    scenes = [
        Scene(index=i, t_in=_r(a, 3), t_out=_r(b, 3)) for i, (a, b) in enumerate(itertools.pairwise(bounds))
    ]

    # ---- audio per second --------------------------------------------------------------------------
    loud_s = silent_s = speech_s = music_s = None
    if audio is not None:
        loud_s = np.full(n, -120.0)
        if audio.loudness.size:
            lidx = np.clip(np.floor(audio.loudness_times).astype(int), 0, n - 1)
            energy = np.power(10.0, audio.loudness / 10.0)
            for s in range(n):
                chunk = energy[lidx == s]
                if chunk.size:
                    loud_s[s] = max(-120.0, 10.0 * math.log10(float(chunk.mean()) + 1e-12))
        silent_s = ranges_overlap_seconds(audio.silences, n) > 0.5
        sf = audio.speech_frames
        sidx = np.clip(np.floor((np.arange(sf.size) + 0.5) * VAD_FRAME_S).astype(int), 0, n - 1)
        speech_s = np.array([float(sf[sidx == s].mean()) if (sidx == s).any() else 0.0 for s in range(n)])
        music_s = ranges_overlap_seconds(audio.music_ranges, n) > 0.5

    # ---- per-second rows ---------------------------------------------------------------------------
    by_second: dict[int, list[SubjectSample]] = {}
    for sample in subjects.samples:
        by_second.setdefault(min(int(sample.t), n - 1), []).append(sample)

    rows: list[SecondMetrics] = []
    for s in range(n):
        sel = fidx == s
        best = _best_sample(by_second.get(s, []))
        subject = best.subject if best else None
        center = best.subject_center if best else None
        exposure = "ok"
        if _majority(frames.under, fidx, s):
            exposure = "under"
        elif _majority(frames.over, fidx, s):
            exposure = "over"
        rows.append(
            SecondMetrics(
                t=s,
                motion=_r(float(np.clip(frames.motion[sel].mean(), 0, 1)) if sel.any() else 0.0),
                sharpness=_r(_nanmedian(frames.sharpness[sel]), 3),
                blurry=_majority(blurry, fidx, s),
                shake=_r(float(shake[sel].mean()) if sel.any() else 0.0, 5),
                shaky=_majority(shaky, fidx, s),
                brightness=_r(float(frames.brightness[sel].mean()) if sel.any() else 0.0, 3),
                exposure=exposure,
                black=_majority(frames.black, fidx, s),
                scene_cut=any(s <= c < s + 1 for c in cuts),
                faces=[Box(x=_r(f.x), y=_r(f.y), w=_r(f.w), h=_r(f.h), score=_r(f.score, 3)) for f in (best.faces if best else [])][:5],
                person_count=max((len(x.bodies) for x in by_second.get(s, [])), default=0),
                subject=Box(x=_r(subject.x), y=_r(subject.y), w=_r(subject.w), h=_r(subject.h), score=_r(subject.score, 3)) if subject else None,
                subject_center=Point(x=_r(min(max(center[0], 0), 1)), y=_r(min(max(center[1], 0), 1))) if center else None,
                loudness_lufs=_r(float(loud_s[s]), 1) if loud_s is not None else None,
                silent=bool(silent_s[s]) if silent_s is not None else None,
                speech=_r(float(speech_s[s]), 3) if speech_s is not None else None,
                music=bool(music_s[s]) if music_s is not None else None,
                dead=bool(dead_cover[s] > 0.5),
            )
        )  # fmt: skip

    # ---- summary -----------------------------------------------------------------------------------
    dead_seconds = sum(b - a for a, b in dead_ranges)
    total_frames = max(ft.size, 1)
    audio_summary = None
    if audio is not None:
        audio_summary = AudioSummary(
            integrated_lufs=audio.integrated_lufs,
            loudness_range_lu=audio.loudness_range_lu,
            peak_dbfs=audio.peak_dbfs,
            speech_ratio=_r(min(1.0, sum(b - a for a, b in audio.speech_ranges) / duration), 3),
            music_ratio=_r(min(1.0, sum(b - a for a, b in audio.music_ranges) / duration), 3),
            tempo_bpm=audio.tempo_bpm,
        )
    summary = Summary(
        dead_ratio=_r(min(1.0, dead_seconds / duration), 3),
        dead_seconds=_r(dead_seconds, 2),
        usable_seconds=_r(max(0.0, duration - dead_seconds), 2),
        video=VideoSummary(
            mean_motion=_r(float(frames.motion.mean()) if ft.size else 0.0),
            median_sharpness=_r(_nanmedian(frames.sharpness), 3),
            blurry_ratio=_r(float(blurry.sum()) / total_frames, 3),
            shaky_ratio=_r(float(shaky.sum()) / total_frames, 3),
            face_ratio=_r(sum(1 for r in rows if r.faces) / n, 3),
            person_ratio=_r(sum(1 for r in rows if r.person_count or r.faces) / n, 3),
            scene_count=len(scenes),
        ),
        audio=audio_summary,
    )

    detectors = {"scenes": "pyscenedetect.adaptive", "video": "opencv.frame_stats", **subjects.detectors}
    if audio is not None:
        detectors.update(audio.detectors)
    params: dict[str, float | int | str | bool] = {
        **frames.params,
        "dead_motion": DEAD_MOTION,
        "min_dead_s": MIN_DEAD_S,
        "min_subject_hold_s": MIN_SUBJECT_HOLD_S,
        "dead_speech_pad_s": DEAD_SPEECH_PAD_S,
    }
    if audio is not None:
        params["silence_threshold_lufs"] = audio.silence_threshold_lufs

    return Signals(
        clip_id=clip_id,
        source_sha256=sha256,
        media=info,
        analysis=AnalysisInfo(
            pipeline_version=pipeline_version,
            analysis_fps=ANALYSIS_FPS,
            analysis_width=frames.width,
            detectors=detectors,
            timings_s={k: _r(v, 3) for k, v in timings.items()},
            params=params,
        ),
        per_second=rows,
        scenes=scenes,
        scene_cuts=[_r(c, 3) for c in cuts],
        silences=[Range(t_in=a, t_out=b) for a, b in silences],
        speech_ranges=[Range(t_in=a, t_out=b) for a, b in (audio.speech_ranges if audio else [])],
        music_ranges=[Range(t_in=a, t_out=b) for a, b in (audio.music_ranges if audio else [])],
        beats=audio.beats if audio else [],
        dead_zones=dead_zones,
        quality_flags=flags,
        summary=summary,
    )
