# Decisions

Every default chosen and every deviation from the PRD, with the reason. Newest milestone last.

## M0 — Skeleton

| # | Decision | Why |
|---|---|---|
| D-001 | **Python 3.12, uv workspace monorepo** (`packages/schemas`, `packages/pipeline`, `apps/api`), one lockfile. | PRD asks for 3.11+. 3.12 has wheels for every media/ML dependency (MediaPipe included); uv gives reproducible, fast installs and a frozen lock for Docker and CI. |
| D-002 | **Pydantic v2 is the single source of truth** for EDL, Composition, signals and media info. JSON Schema is exported to `packages/schemas/json/` for the web client (Zod/TS codegen) and LLM prompts. | One definition, two consumers. `extra="forbid"` everywhere so malformed model output fails loudly; `SchemaValidationError.feedback` is a compact message designed for the "retry once with the error" rule (PRD §10). |
| D-003 | EDL validation goes beyond types: referential integrity (clip and segment ids), `src_out` within clip duration, adjacent-only transitions, transition shorter than both segments, every output-timeline item inside the output duration, captions sorted and non-overlapping. Soft issues (`lint_edl`) are warnings, not errors. | PRD §12 automated checks ("no gaps or overlaps") start at the schema. Lints feed the Edit Report and the self-review loop instead of rejecting legal edits. |
| D-004 | Composition schema shipped now (M13 is when it gets used): every `Claim` requires a non-empty `source`; scenes must tile the timeline with no gaps; `footage` scenes require `clip_id/src_in/src_out`. | Fact grounding is enforced by the contract, not just by the prompt. |
| D-005 | **RQ + Redis** for jobs (not Celery, not Temporal). Jobs are idempotent and retried with backoff (15 s, 60 s, 180 s); `MediaError` (a bad file) is permanent and not retried. | Simplest option that meets "idempotent, resumable, retry with backoff". Pipeline stages are short, independent jobs, so durable workflows are not needed yet. Revisit if M3+ needs long multi-step orchestration. |
| D-006 | **SQLite by default for zero-config local dev and tests; Postgres in Compose/production**, schema managed by Alembic (initial migration generated against Postgres 16). | Anyone can run the API with no services. Tests run in seconds. Postgres-only behaviour (row locks) has its own integration tests. |
| D-007 | **Docker:** one image for API and worker, `python:3.12-slim-bookworm`, uv installed from PyPI, MediaPipe models baked in at build time, runs as non-root uid 10001 under tini. A single OpenCV build is forced via `override-dependencies`. | PRD §7. Baking models means workers never download at runtime. MediaPipe and PySceneDetect otherwise pull three conflicting OpenCV wheels that ship the same `cv2` module (image dropped from 4.98 GB to 2.44 GB). |
| D-008 | CI: lint (ruff format + ruff + mypy), tests with Postgres and Redis service containers (unit, media ground-truth regression, API, integration), and a Docker build. | PRD §12 "regression suite runs in CI on a few short clips". The clips are synthesised, so CI needs no fixtures checked in. |

## M1 — Ingest and signals

| # | Decision | Why |
|---|---|---|
| D-101 | **Resumable uploads use a tus-style offset protocol** on our own endpoints (`POST /v1/uploads`, `PATCH` with `Upload-Offset`, `HEAD` to resume, `POST .../complete`), as PRD §8 lists. The server's offset is the staging file's size, so a crash mid-chunk can never desync it. Optional end-to-end SHA-256 on complete. | Matches the PRD's API shape exactly, works through any proxy, and needs no extra server. **Scale note:** staging is a local or shared volume; for multiple API replicas switch to S3 multipart with presigned part URLs (same client flow). |
| D-102 | **Probe at upload completion, not in the worker.** | The total-length meter and the limit check need the duration the moment a clip is added. Corrupt or non-video files are rejected with a reason before they ever reach a project. |
| D-103 | **Limits:** total raw length summed across a project's clips; HTTP **402** `total_length_limit_exceeded` (with current, adding, limit, plan, `upgrade_available`), 409 `clip_limit_exceeded`, 402 `project_quota_exceeded`. Adding clips is all-or-nothing. The project row is locked (`SELECT … FOR UPDATE`) during the check. | PRD §5.1b. The lock is verified by a mutation test: without it, concurrent adds both slip under the limit (3/3 failures); with it, 3/3 passes. |
| D-104 | **Proxy:** display-oriented (rotation applied), short edge 540 px (never upscaled), constant frame rate ≤ 30 fps, H.264 CRF 26, AAC 96 kb/s, faststart. | PRD §5.1. CFR gives exact analysis timestamps; faststart lets the preview stream. |
| D-105 | **Signal passes run concurrently** (video stats, subjects, scenes, audio in threads; clips in parallel processes or worker-pool jobs). Measured on 4 vCPU: 6 min of mixed footage in 33 s via CLI; 3 clips via the API in 21 s with 3 workers. Stage A AI cost: **$0**. | PRD §6 "first preview in < 5 min for 5 min of video" leaves ample budget for M2–M4. |
| D-106 | **Motion** = share of pixels changed (> 10/255 after a 5×5 blur) at 10 fps, 384 px wide. | Calibrated on real clips: robust to compression noise yet sensitive to small subjects (a person far from a static camera registers 0.5–2 %). |
| D-107 | **Blur** = `1 − var(Lap(gauss₁(f))) / var(Lap(f))` (content-normalised sharpness 0–1); blurry if < 0.75 or > 0.08 below the clip's median. Flat frames are excluded. | Raw Laplacian variance depends on content: the same colour bars varied 2×, falsely flagging blur. Measured: sharp footage 0.90–0.94, soft webcam 0.78–0.84, visibly blurred ≤ 0.70. |
| D-108 | **Shake** = camera-path jitter (path minus its 1 s moving average) from phase correlation, counted **only when the correlation response is ≥ 0.8**, normalised by the frame diagonal. | Camera jitter yields a confident global shift (response ≥ 0.97); moving subjects do not (0.2–0.68). This removed every false positive (8 → 0) on the ground-truth clip while still catching real hand-held shake. |
| D-109 | **Speech and music:** MediaPipe **YAMNet** (AudioSet classifier) for speech/music probability, **webrtcvad** for 30 ms speech boundaries, gated by YAMNet so VAD does not fire on music. Loudness: ffmpeg's EBU R128 meter (momentary + integrated + LRA). Beats: librosa, reported only inside music. | YAMNet scored 0.94 "Speech" on synthetic speech and separates music reliably; VAD alone flags music as speech. **Known limit:** speech onsets right at a music boundary can be ~0.6 s early; M2's Whisper word timestamps replace these boundaries. |
| D-110 | **Faces and persons:** MediaPipe BlazeFace (short range) + Pose Landmarker (lite, up to 4 people) at 2 fps; the subject box is the primary face joined with its body. OpenCV Haar + HOG fallback; if no detector loads, analysis continues without subject data. Used for framing only; no embeddings, no identification. | PRD §5.2 and §11. A missing model degrades quality but never fails a job (found and fixed during container testing: models were saved 0600 and unreadable by the non-root worker). |
| D-111 | **Dead zones are graded:** `black_frames`, `static_empty` (no subject), and `static_subject_hold` (a visible person holding still for ≥ 3 s). **Extension of PRD §5.2**, which defines dead zones as "low motion and no speech". | Stage C skips dead zones, so a person holding a pose (a smile, a product shot) must not be dropped unseen. Policy for M3: skip `black_frames` and `static_empty`; sample `static_subject_hold` sparsely. |
| D-112 | **Analysis cache:** content-hash keyed (sha256 + pipeline version) in the CLI; in the API, reuse is **scoped to the same user** (a re-added or duplicated clip is copied, not recomputed). | Re-render and re-add are instant, and deleting one user's data can never affect another's. |
| D-113 | **Storage:** `local` (dev/tests) or `s3` (Cloudflare R2 preferred, MinIO in Compose, AWS). Keys live under `users/{id}/…` so deletion is a prefix sweep. S3 writes use SSE-AES256 (encryption at rest); proxies are served by a presigned redirect. | PRD §6/§7. MinIO needs a KMS for SSE, so the dev Compose stack disables it; R2/S3 production keeps it on. |
| D-114 | **Auth stub until M10:** the caller is identified by an `X-User-Id` header (default `usr_dev`) while `AUTOEDIT_DEV_AUTH=true`. Every query is already scoped to the user. **Production must set `AUTOEDIT_DEV_AUTH=false`** until M10 adds sign-in. | Lets M1–M9 be exercised end to end without building accounts first (PRD build order). |
| D-115 | **Motion mode** is accepted by the schema but `POST /v1/projects` with `mode: "motion"` returns 409 `mode_not_available` until M16. | PRD §14 M9: "Motion mode shown as coming soon until M16". |
| D-116 | **Progress:** SSE (`GET /v1/projects/{id}/events`) built by polling project state once a second, closing with a `settled` event. | Works with any number of API and worker processes without a pub/sub dependency. Swap to Redis pub/sub if load requires. |
| D-117 | **Retention:** `python -m autoedit_api.manage purge` deletes projects older than `AUTOEDIT_RETENTION_DAYS` (30) and abandoned uploads (48 h). | PRD §6. Schedule it daily (cron or a platform scheduler) in deployment. |

## Deferred, with a home

| Item | PRD | Lands in |
|---|---|---|
| Paste links as clip sources | §4.1 | With the M9 upload UI (needs the same safe fetcher as M12's crawler). |
| Upload content moderation (NSFW/abuse screen) | §11 | M11 hardening, as a Stage A pre-check. |
| Real sign-in (email/phone, Google) | §5.8 | M10. |

## Open question for M10

PRD §5.8 names **Stripe as the primary global processor**, with Paystack/Flutterwave added later for
regional methods, but the M10 milestone lists only **Paystack/Flutterwave**. Default unless told
otherwise: Stripe first, with Paystack/Flutterwave behind the same provider interface.
