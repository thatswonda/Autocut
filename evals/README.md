# Evals

PRD §12. The eval set grows milestone by milestone.

| Set | Status | What it checks |
|---|---|---|
| Synthetic ground truth (`autoedit_pipeline.synth`) | **live (M1)** | Every Stage A signal against known answers: cuts, black, static, speech, silence, music, beats, shake, rotation. Runs in CI on every push (`packages/pipeline/tests/test_signals.py`). |
| Real-footage smoke clips | **live (M1)** | CC-BY 4.0 Intel sample videos (talking head, empty-room/person, classroom). Used for manual calibration; see `DECISIONS.md` D-008. |
| 20+ sample videos (silent action, talking head, tutorial, product demo, vlog, noisy audio, vertical/horizontal) | M11 | Dead time removed, mid-sentence/mid-action cut rate (~0), caption WER, human 1–5 rating, cost and time per minute. |
| Motion graphics businesses (20+) | M15 | Text overflow rate (0), brand fidelity, claim traceability (100%). |

Real clips used during M1 calibration (CC-BY 4.0, https://github.com/intel-iot-devkit/sample-videos):
`head-pose-face-detection-female.mp4`, `one-by-one-person-detection.mp4`, `classroom.mp4`.
