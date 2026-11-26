"""Where is the subject? Face boxes and person/pose boxes for smart cropping and reframing.

Used for framing only. Nothing here identifies people, and no face embeddings are computed or stored.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from autoedit_schemas.media import MediaInfo

from ..media import frame_spec, iter_frames
from ..models import model_path
from ..native import quiet_native_stderr

log = logging.getLogger(__name__)

SUBJECT_FPS = 2.0
SUBJECT_WIDTH = 960
MIN_FACE_SCORE = 0.6
MAX_POSES = 4
LANDMARK_VISIBILITY = 0.5


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def clamp(self) -> Box:
        x0, y0 = max(0.0, self.x), max(0.0, self.y)
        x1, y1 = min(1.0, self.x + self.w), min(1.0, self.y + self.h)
        return Box(x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0), min(max(self.score, 0.0), 1.0))

    def union(self, other: Box) -> Box:
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        x1 = max(self.x + self.w, other.x + other.w)
        y1 = max(self.y + self.h, other.y + other.h)
        return Box(x0, y0, x1 - x0, y1 - y0, max(self.score, other.score))

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


@dataclass
class SubjectSample:
    t: float
    faces: list[Box]
    bodies: list[Box]

    @property
    def subject(self) -> Box | None:
        face = max(self.faces, key=lambda b: b.area) if self.faces else None
        if face is not None:
            cx, cy = face.center
            body = next((b for b in self.bodies if b.contains(cx, cy)), None)
            return (face.union(body) if body else face).clamp()
        if self.bodies:
            return max(self.bodies, key=lambda b: b.area).clamp()
        return None

    @property
    def subject_center(self) -> tuple[float, float] | None:
        """Face centre when visible (what a crop should keep), else the head region of the body."""
        if self.faces:
            return max(self.faces, key=lambda b: b.area).center
        if self.bodies:
            b = max(self.bodies, key=lambda b: b.area)
            return b.x + b.w / 2, b.y + b.h * 0.2
        return None


@dataclass
class SubjectTrack:
    samples: list[SubjectSample] = field(default_factory=list)
    detectors: dict[str, str] = field(default_factory=dict)


class _MediaPipeDetectors:
    def __init__(self, face_model: Path | None, pose_model: Path | None):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        self.face: Any = None
        self.pose: Any = None
        if face_model is not None:
            self.face = vision.FaceDetector.create_from_options(
                vision.FaceDetectorOptions(
                    base_options=BaseOptions(model_asset_path=str(face_model)),
                    running_mode=vision.RunningMode.VIDEO,
                    min_detection_confidence=MIN_FACE_SCORE,
                )
            )
        if pose_model is not None:
            self.pose = vision.PoseLandmarker.create_from_options(
                vision.PoseLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(pose_model)),
                    running_mode=vision.RunningMode.VIDEO,
                    num_poses=MAX_POSES,
                    min_pose_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
            )

    def detect(self, rgb: npt.NDArray[np.uint8], t_ms: int) -> tuple[list[Box], list[Box]]:
        h, w = rgb.shape[:2]
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        faces: list[Box] = []
        bodies: list[Box] = []
        if self.face is not None:
            for d in self.face.detect_for_video(image, t_ms).detections:
                bb = d.bounding_box
                score = float(d.categories[0].score) if d.categories else 1.0
                faces.append(
                    Box(bb.origin_x / w, bb.origin_y / h, bb.width / w, bb.height / h, score).clamp()
                )
        if self.pose is not None:
            for landmarks in self.pose.detect_for_video(image, t_ms).pose_landmarks:
                pts = [
                    (lm.x, lm.y)
                    for lm in landmarks
                    if (lm.visibility if lm.visibility is not None else 1.0) >= LANDMARK_VISIBILITY
                ]
                if len(pts) < 4:
                    continue
                xs, ys = zip(*pts, strict=True)
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                pad_x, pad_y = (x1 - x0) * 0.1, (y1 - y0) * 0.08
                box = Box(x0 - pad_x, y0 - pad_y * 2, (x1 - x0) + 2 * pad_x, (y1 - y0) + 3 * pad_y)
                bodies.append(box.clamp())
        return faces[:5], bodies

    def close(self) -> None:
        for det in (self.face, self.pose):
            if det is not None:
                with contextlib.suppress(Exception):
                    det.close()


class _OpenCVDetectors:
    """Classical fallback when MediaPipe or its models are unavailable."""

    def __init__(self) -> None:
        cascade = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"  # type: ignore[attr-defined]
        self.face = cv2.CascadeClassifier(str(cascade))
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())  # type: ignore[attr-defined]

    def detect(self, rgb: npt.NDArray[np.uint8], t_ms: int) -> tuple[list[Box], list[Box]]:
        del t_ms
        h, w = rgb.shape[:2]
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        faces = [
            Box(x / w, y / h, fw / w, fh / h, 0.7).clamp()
            for x, y, fw, fh in self.face.detectMultiScale(gray, 1.1, 5, minSize=(24, 24))
        ]
        rects, weights = self.hog.detectMultiScale(gray, winStride=(8, 8))
        bodies = [
            Box(x / w, y / h, bw / w, bh / h, float(min(1.0, max(0.0, wt)))).clamp()
            for (x, y, bw, bh), wt in zip(rects, np.ravel(weights), strict=False)
            if wt > 0.5
        ]
        return faces[:5], bodies

    def close(self) -> None:
        return None


def track_subjects(proxy: Path, info: MediaInfo) -> SubjectTrack:
    face_model, pose_model = model_path("face"), model_path("pose")
    detector: _MediaPipeDetectors | _OpenCVDetectors
    track = SubjectTrack()
    try:
        if face_model is None and pose_model is None:
            raise RuntimeError("no MediaPipe models available")
        with quiet_native_stderr():
            detector = _MediaPipeDetectors(face_model, pose_model)
        track.detectors = {
            "faces": "mediapipe.blaze_face_short_range" if face_model else "none",
            "persons": "mediapipe.pose_landmarker_lite" if pose_model else "none",
        }
    except Exception as exc:
        log.warning("MediaPipe unavailable (%s); falling back to OpenCV detectors", exc)
        try:
            detector = _OpenCVDetectors()
            track.detectors = {"faces": "opencv.haar_frontalface", "persons": "opencv.hog_people"}
        except Exception as exc2:
            # Subject data improves framing but is not essential: never fail a clip over it.
            log.error("no subject detector available (%s); continuing without faces/persons", exc2)
            track.detectors = {"faces": "none", "persons": "none"}
            return track

    spec = frame_spec(info, SUBJECT_FPS, SUBJECT_WIDTH)
    try:
        for i, (t, rgb) in enumerate(iter_frames(proxy, spec)):
            if i < 2:  # first inferences print one-off native warnings
                with quiet_native_stderr():
                    faces, bodies = detector.detect(rgb, round(t * 1000))
            else:
                faces, bodies = detector.detect(rgb, round(t * 1000))
            track.samples.append(SubjectSample(t=t, faces=faces, bodies=bodies))
    finally:
        with quiet_native_stderr():
            detector.close()
    return track
