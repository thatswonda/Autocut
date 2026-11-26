# AutoEdit

Raw footage in, finished edit out. AutoEdit watches every frame, listens to the audio, understands the
story, and produces an **Edit Decision List** (EDL): a JSON document that a deterministic FFmpeg
renderer turns into video. The AI never touches pixels, so every decision is visible, editable and
cheap to re-render. A second mode turns a business's website, app or product photos into motion
graphics, planned as a schema-validated **Composition** and rendered the same way.

Full spec: [`docs/PRD.md`](docs/PRD.md) · Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md)

## Status

| Milestone | State |
|---|---|
| **M0** Skeleton: monorepo, schemas (EDL, Composition, signals), Docker, Compose, CI | ✅ done |
| **M1** Ingest and signals: multi-clip projects, resumable uploads, probe, proxy, total-length limits, local signal pass, `signals.json`, CLI | ✅ done |
| M2 Transcription · M3 Vision + story · M4 EDL + render · … · M18 | next |

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), FFmpeg 6+, and on Linux `libegl1` (MediaPipe's headless runtime).

```bash
uv sync
```

```bash
uv run autoedit models download
```

### Analyze clips from the command line

```bash
uv run autoedit analyze clip1.mp4 clip2.mov clip3.mkv -o autoedit-out
```

You get a live progress bar per clip (clips run in parallel), a summary table, and
`autoedit-out/<clip>/signals.json` plus `autoedit-out/project.json`. Add `--plan free` to enforce the
free plan's 3-minute total limit, `--json` for machine output.

No footage at hand? `make demo` synthesises a 30-second clip with known ground truth (black, speech,
static, music, hand-held shake) and analyzes it.

Other commands: `autoedit probe FILE`, `autoedit validate edl FILE.json`, `autoedit schema export DIR`,
`autoedit models list`.

### Run the API

Zero services (SQLite, jobs run in-process):

```bash
AUTOEDIT_QUEUE_MODE=inline uv run uvicorn autoedit_api.main:app --reload
```

Full stack (API, worker pool, Postgres, Redis, MinIO):

```bash
docker compose -f infra/docker-compose.yml up --build
```

Then drive it exactly as the web app will:

```bash
uv run python scripts/api_demo.py clip1.mp4 clip2.mov --api http://localhost:8000
```

Interactive API docs: http://localhost:8000/docs

## API (v1)

| Method & path | Purpose |
|---|---|
| `POST /v1/uploads` | Start a resumable upload (`filename`, `size_bytes`) |
| `PATCH /v1/uploads/{id}` | Append a chunk at `Upload-Offset` (409 returns the server offset to resume from) |
| `HEAD /v1/uploads/{id}` | Current offset, for resuming after a dropped connection |
| `POST /v1/uploads/{id}/complete` | Verify size (and optional SHA-256), probe, store |
| `POST /v1/projects` | Create from the start screen: `mode`, `prompt`, `options`, `upload_ids` |
| `GET /v1/projects`, `GET /v1/projects/{id}` | List; status, per-clip stage and progress, total-length meter |
| `POST /v1/projects/{id}/clips` | Add a clip (rejected with **402** if the total raw length would exceed the plan) |
| `DELETE /v1/projects/{id}/clips/{clip_id}` | Remove a clip |
| `PUT /v1/projects/{id}/clips/order` | Reorder |
| `GET /v1/projects/{id}/clips/{clip_id}/signals` | The clip's `signals.json` |
| `GET /v1/projects/{id}/clips/{clip_id}/proxy` | 540p preview (HTTP range / presigned redirect) |
| `GET /v1/projects/{id}/events` | Server-sent progress events |
| `DELETE /v1/projects/{id}` | Delete the project and all derived data |
| `GET /v1/me`, `/healthz`, `/readyz` | Plan and limits; liveness; readiness (DB + queue) |

Errors always look like `{"error": {"code", "message", "details"}}`.

## What `signals.json` contains

Stage A of the understanding engine: local analysis only, **$0 AI cost**, about 0.1× real time.

| Section | Contents |
|---|---|
| `media` | ffprobe facts: container, codecs, size, rotation, display size, fps, VFR, audio |
| `per_second[]` | motion, sharpness, blurry, shake, brightness, exposure, black, scene cut, faces (boxes), person count, subject box and center (for reframing), loudness (LUFS), silent, speech share, music, dead |
| `scenes`, `scene_cuts` | Hard cuts (PySceneDetect, adaptive) |
| `speech_ranges`, `silences`, `music_ranges`, `beats` | VAD gated by YAMNet; EBU R128 loudness; beats inside music only |
| `dead_zones` | `black_frames`, `static_empty`, `static_subject_hold` (graded so a person holding still is never skipped unseen) |
| `quality_flags` | blur, shake, black, under/over-exposure ranges |
| `summary` | dead ratio, usable seconds, face/person ratios, integrated loudness, tempo |
| `analysis` | which detector produced each signal, thresholds used, per-pass timings |

## Repository

```
apps/api            FastAPI app, uploads, projects, worker jobs, Alembic migrations
apps/web            Next.js UI (M9)
packages/schemas    EDL, Composition, signals, media info: the single source of truth (+ JSON Schema)
packages/pipeline   ingest, probe, proxy, Stage A signals, CLI (later: transcribe, vision, story, edl, review)
packages/render     EDL -> FFmpeg (M4-M6)
packages/assets     b-roll, music, SFX providers (M7)
packages/brand      source intake, brand kit (M12)
packages/motion     templates, composition renderer (M13-M15)
infra               Dockerfile, docker-compose.yml
docs                PRD, DECISIONS, PROMPTS
evals               evaluation sets and status
scripts             api_demo.py (end-to-end client)
```

## Development

```bash
make lint
```

```bash
make test
```

`make test` runs unit tests, the media ground-truth regression suite and API tests (about a minute, no
services needed). `make test-all` adds the Postgres/Redis integration tests; set
`AUTOEDIT_IT_DATABASE_URL` and `AUTOEDIT_IT_REDIS_URL` first. All configuration is environment
variables with the `AUTOEDIT_` prefix; see [`.env.example`](.env.example).

Sample footage used for calibration: CC-BY 4.0 clips from
[intel-iot-devkit/sample-videos](https://github.com/intel-iot-devkit/sample-videos).

Terms and privacy copy are placeholders pending legal review (PRD §11).
