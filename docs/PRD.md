# PRD: AutoEdit (working title), an AI Video Editor and Motion Graphics Creator

> Instructions for Claude Code: Read this whole document first. Build in the order given in Section 14 (Build Order). Finish and test each milestone before starting the next. Ask me only when a decision blocks you; otherwise pick the default stated here and note it in `DECISIONS.md`.

---

## 1. Overview

AutoEdit turns raw footage into a finished, polished video automatically. The user uploads one or more raw files (with or without audio). The system watches the video frame by frame, listens to the audio, understands what the video is about, removes irrelevant parts, keeps and tightens the important parts, and adds captions, effects, color grading, overlays, b-roll, and sound effects. The user previews the result and can adjust any decision before exporting.

**Core principle:** the AI never edits pixels directly. It produces an **Edit Decision List (EDL)**, a structured JSON document. A deterministic render engine (FFmpeg) turns the EDL into the video. Everything is therefore editable, reproducible, and cheap to re-render.

**Two modes, one app:** from one start screen (a chat box), the user chooses either **Edit video** (turn raw footage into a finished edit) or **Make motion graphics** (create an animated promo for a business from its website, app, product photos, or files). Both modes use the same pattern: Claude plans in structured JSON (the EDL for editing, a Composition for motion graphics) and a deterministic engine renders it. Both share accounts, credits, preview, captions, audio, and export.

## 2. Goals and Non-Goals

**Goals**
- One-step "drop file, get a finished edit" experience.
- Works on silent videos (vision-first) and on talking videos (audio plus vision).
- Every AI decision is visible and editable by the user.
- Runs affordably and reliably for users worldwide, including on weak devices and slow networks.
- Supports projects made of multiple clips, edited together as one video.
- One entry point (a chat box) where the user picks Edit video or Make motion graphics, adds the relevant materials, writes a prompt, and proceeds.
- Generates motion graphics for any business from its website, app listing, product photos, or uploaded files, with every factual claim traceable to a source.

**Non-Goals (v1)**
- A full manual timeline editor on par with Premiere or DaVinci.
- Real-time on-device rendering on phones.
- Multi-user collaboration.
- Generative video (AI-created footage).

## 3. Target Users

1. Social creators (TikTok, Reels, Shorts, YouTube) with raw phone footage.
2. Small business owners and marketplace sellers making product, promo, and motion graphics videos from their website, app, or product photos.
3. Agencies or editors who want a fast first cut.

The product is global. Make no region-specific assumptions in UX, language, currency, or payments.

## 4. User Flow

### 4.0 Start Screen (Mode Chooser)
The home screen is a single chat box with a mode selector:
- **Edit video:** turn raw footage into a finished edit.
- **Make motion graphics:** create an animated video for a business from its website, app, product photos, or files.

After the user picks a mode, the attachment area adapts to that mode (details in Section 5.9). The user adds the relevant materials, types a prompt with the relevant information, and presses send to proceed.

### 4.1 Flow A: Edit Video

1. Upload one or more videos (resumable, chunked) or paste links. A live meter shows the total raw length across all clips against the plan limit. The user can reorder or remove clips.
2. Choose options: goal (Short-form vertical / YouTube / Promo), target length (optional), style preset, language, and toggles (captions, b-roll, sound effects, color grade, music).
3. Processing screen with live stage progress (Analyzing, Understanding, Cutting, Enhancing, Rendering).
4. Preview the result, with an AI "Edit Report" listing what was cut and why.
5. Adjust: toggle or edit any cut, caption, overlay, effect, grade, or sound effect; swap b-roll; change the preset. Re-render takes seconds because the analysis is cached.
6. Export (resolutions, aspect ratios) and share.

### 4.2 Flow B: Make Motion Graphics
1. In the chat box, the user selects **Make motion graphics** and adds materials: website URLs, an app store link or app screenshots, product photos, and/or files (logo, brand guide, deck, price list).
2. The user types a prompt with the relevant information: what the business does, the goal (promo, product launch, explainer, ad), audience, tone, language, length, aspect ratio, and the call to action.
3. The user confirms they own or are authorized to use the brand and assets, then sends.
4. Processing stages with live progress: reading sources, building the brand kit, understanding the business.
5. The user reviews and approves the **brand kit** (logo, colors, fonts) and the **script and storyboard**, which show the source for every factual claim. They can edit or request changes before anything renders.
6. Rendering, with live stage progress, then a preview with a scene-by-scene list.
7. Adjust: edit text, swap images, change template, timing, colors, fonts, or voice, regenerate a single scene with a note, then re-render.
8. Export and share.

## 5. Functional Requirements

### 5.1 Ingest
- Chunked, resumable uploads (tus protocol or S3 multipart) with retry on bad networks.
- Accepted formats: mp4, mov, mkv, webm.
- Probe each clip with ffprobe: duration, fps, resolution, rotation, audio presence, codecs.
- Create a low-res proxy (e.g., 540p) per clip for analysis and preview. Keep the originals for the final render.

### 5.1b Multi-Clip Projects
- A project holds 1 to N clips (default max 20, configurable). Users can add, remove, and reorder clips before and after processing.
- **The limit is on total raw length.** The plan limit applies to the sum of all clip durations in the project (defaults: free 3 min total, paid 30 min total, configurable). Show a live total-length meter. Block adding a clip that would exceed the limit, with a clear message and an upgrade option. Credits and billing use the same total.
- Clips may differ in resolution, fps, aspect ratio, orientation, codec, and audio level, and some may be silent while others have audio. Normalize at render time: scale or smart-crop to the target aspect, convert to a common fps, and match loudness.
- Stages A to C run per clip, in parallel, and are cached per clip. Adding a clip analyzes only the new clip. The story pass (Stage D) re-runs at project level.
- **Cross-clip story:** treat all clips as one body of footage. Determine the overall message, keep the user's clip order by default (with a "let AI reorder" toggle), remove duplicate or redundant content across clips, pick the best take when the same thing appears in several clips, and make sure the hook and flow work across the whole project.
- **Cross-clip polish:** match color and exposure across clips, keep loudness and room tone consistent, choose a transition between clips (hard cut or effect), and allow unused or leftover clips to serve as b-roll.

### 5.2 Understanding Engine (vision-first, audio as bonus)

**Stage A: Local signal pass (no AI cost)**
- Scene-change detection (PySceneDetect or ffmpeg scdet).
- Motion score per second (frame differencing or optical flow).
- Blur, shake, black-frame, and over/under-exposure detection.
- Face and person detection plus pose (MediaPipe or similar) to find where the subject is, and the face position for smart cropping and reframing.
- Audio (if present): loudness curve, silence ranges, speech/non-speech (VAD), music detection, beat positions.
- Output: a `signals.json` with per-second metrics and flagged "dead zones" (low motion and no speech).

**Stage B: Transcription (if audio has speech)**
- Whisper (API or self-hosted faster-whisper) with word-level timestamps.
- Detect filler words, false starts, repeated takes, and long pauses.

**Stage C: Coarse visual pass (Claude vision)**
- Sample frames at about 1 fps, downscaled (~512 px on the long edge), in timestamped batches. Skip frames inside obvious dead zones.
- Claude returns a structured per-segment description: what is happening, subjects, actions, emotion, quality notes.
- Output: one `timeline.json` per clip, a scene-by-scene description of that clip.

**Stage D: Story pass (Claude)**
- Input: every clip's `timeline.json`, transcript, and `signals.json`, the clip order, and user options.
- Claude determines: topic and message, the structure (hook, body, payoff), the importance score of each segment, which segments are filler, repetition, or failed takes, and the best order (usually chronological unless the preset allows reordering).
- Output: `story.json`.

**Stage E: Fine pass on cut boundaries**
- Around each proposed cut, sample at 4 to 8 fps so Claude can choose exact in/out points (for example, cut right after a gesture completes, not mid-motion).
- Snap final cut times to the nearest frame, speech boundary, or beat using the local signals.

### 5.3 Edit Decision List (EDL)

Claude outputs the EDL as JSON validated against a strict schema (use Pydantic or Zod). Reject and retry on invalid output. Minimum schema:

```json
{
  "version": 1,
  "meta": { "title": "", "summary": "", "aspect": "9:16", "total_raw_duration_s": 240, "target_duration_s": 45, "style": "energetic" },
  "clips": [
    { "id": "c1", "order": 1, "duration_s": 120.0 },
    { "id": "c2", "order": 2, "duration_s": 120.0 }
  ],
  "segments": [
    { "id": "s1", "clip_id": "c1", "src_in": 12.40, "src_out": 18.90, "speed": 1.0, "reason": "Hook: subject reveals product" }
  ],
  "removed": [
    { "clip_id": "c1", "src_in": 0.0, "src_out": 12.4, "reason": "Setup, no action, camera adjusting" }
  ],
  "transitions": [
    { "between": ["s1", "s2"], "type": "cut|crossfade|whip|zoom", "duration_s": 0.2 }
  ],
  "captions": {
    "enabled": true,
    "style": "word_pop",
    "words": [ { "t_in": 0.0, "t_out": 0.4, "text": "Hello", "emphasis": false } ]
  },
  "effects": [
    { "t_in": 3.1, "t_out": 3.6, "type": "punch_zoom|shake|slowmo|freeze|speed_ramp", "params": {}, "reason": "" }
  ],
  "overlays": [
    { "t_in": 5.0, "t_out": 8.0, "type": "text|lower_third|emoji|arrow|logo", "content": "", "position": "bottom" }
  ],
  "broll": [
    { "t_in": 10.0, "t_out": 13.0, "query": "busy market street", "source": "pexels", "asset_id": null }
  ],
  "audio": {
    "music": { "enabled": true, "mood": "upbeat", "asset_id": null, "duck_under_speech": true },
    "sfx": [ { "t": 3.1, "type": "whoosh|impact|pop|ding", "asset_id": null, "reason": "" } ],
    "normalize_lufs": -14,
    "denoise": true
  },
  "color": { "preset": "warm_cinematic", "exposure": 0, "contrast": 0, "saturation": 0, "temperature": 0, "lut": null }
}
```

Every item carries a `reason` string so the UI can explain decisions. `src_in` and `src_out` are times inside the clip named by `clip_id`. All other `t_in`, `t_out`, and `t` values are on the final output timeline. Segments from different clips can be interleaved in any order.

### 5.4 Enhancement Features
- **Cutting:** silence and dead-air removal, filler-word removal, bad-take and retake removal, jump cuts, speed ramps for boring-but-necessary parts, hook-first reordering (optional by preset).
- **Smart reframing:** auto-crop 16:9 to 9:16 or 1:1 following the subject (face/person tracking, smoothed).
- **Captions:** word-by-word animated, multiple styles, keyword highlighting, multilingual, RTL-safe. For silent videos, optional narration-style or label captions generated from what is seen.
- **Effects:** punch-in zooms on key moments, speed ramps, freeze frames, subtle shake on impacts, transitions. Effects must be driven by detected events (a gesture, a hit, a reveal, a beat), never random.
- **Color grading:** auto-correct (exposure, white balance, contrast) first, then apply a preset look (LUTs or ffmpeg filters). Include a consistency step so all segments match.
- **B-roll:** Claude picks moments and keywords, then the system fetches from Pexels/Pixabay APIs (store license and attribution info). User can swap or remove.
- **Audio:** noise reduction, loudness normalization, music bed with auto-ducking under speech, sound effects placed at visual events and transitions, beat-synced cuts when music is on.
- **Overlays:** text callouts, lower-thirds, emoji, arrows, logo/watermark.
- **Style presets:** e.g., "Energetic Short", "Clean YouTube", "Cinematic", "Product Promo", "Calm Tutorial". A preset sets pacing, cut aggressiveness, caption style, grade, music mood, and effect intensity.

### 5.5 Self-Review Loop
After the first render, sample frames from the output and send them (with the EDL) to Claude to check for: jarring cuts, lost context, leftover dead time, caption overlap with faces or important content, and over-aggressive effects. Claude returns a list of fixes as EDL patches. Apply at most 2 review iterations.

### 5.6 Preview and Editing UI
- Video player with the EDL timeline rendered as tracks (video, b-roll, captions, overlays, effects, sfx, music).
- Click any item to see its reason, edit, delete, or restore removed segments.
- Caption text editing. B-roll swap. Preset switcher. Strength sliders (cut aggressiveness, effect intensity).
- Re-render uses cached analysis only. No re-analysis unless the user changes the source.

### 5.7 Export
- Resolutions: 720p, 1080p, (4K optional paid). Aspect ratios: 9:16, 1:1, 16:9, 4:5.
- H.264 mp4 default. Optional: download EDL JSON and SRT captions.
- Watermark on the free tier.

### 5.8 Accounts, Credits, Billing
- Email or phone sign-in, Google sign-in.
- Credits or minutes-processed model, where usage in Edit mode is the total raw length across all clips in a project, and usage in Motion mode is the output duration (plus a small fixed credit for brand ingestion). Free tier: a few short projects per month in each mode.
- Payments: Stripe as the primary global processor (cards, wallets), multi-currency pricing, and regional payment methods added later through local providers (for example Paystack or Flutterwave for mobile money and bank transfer in regions where they matter).
- Usage tracking per job (frames analyzed, tokens, render seconds) for cost monitoring.

### 5.9 Start Screen and Mode Chooser
- The home screen is a chat box with a mode selector: **Edit video** and **Make motion graphics**. The selected mode is always visible and can be changed.
- The attachment area changes with the mode:
  - **Edit video:** add one or more video files (with the total-length meter from Section 5.1b), plus optional extras such as music, a logo, or a caption language.
  - **Make motion graphics:** add any of: website URLs, app store links, app screenshots, product photos, logo and brand files, and documents (PDF, deck, spreadsheet, text). Each added item shows as a removable chip.
- The prompt box shows mode-specific placeholder text and a few tappable example prompts. The prompt is free text. The AI extracts the settings (goal, audience, tone, length, language, aspect ratio, call to action) and shows them back as editable chips before the job starts.
- Minimum input to send: Edit needs at least one video. Motion needs at least one source (URL, listing, photo, or file) or a clear text description of the business.
- If the user sends without choosing a mode, infer it (videos attached means Edit; URL, photos, or files without video means Motion) and ask for a one-tap confirmation.
- Switching modes keeps the typed prompt. Attachments that do not fit the new mode are set aside with a clear message, never silently deleted.
- If key information is missing, the AI may ask up to 3 short clarifying questions. The user can skip them and let the AI use defaults.
- Both modes create a project, appear in the same project list, and share accounts, credits, previews, and export.
- Optional crossover: Edit mode can add motion graphics intros, outros, and callouts using a brand kit. Motion mode can include the user's own video clips as footage scenes.

### 5.10 Motion Graphics Mode

**Inputs**
- **Website URLs:** render the page in a headless browser, take screenshots, and extract the logo, favicon, colors, fonts, headings, copy, product listings, images, and call to action.
- **App store links and screenshots:** app name, icon, description, screenshots.
- **Product photos:** auto-enhance, crop, background removal, low-resolution warnings, product category detection.
- **Files:** logo (SVG or PNG), brand guidelines, pitch deck or PDF, price list or spreadsheet, scripts, custom fonts.
- **Prompt:** the user's free-text brief.
- **Safe crawling:** public pages only, respect robots.txt, apply rate limits and timeouts, block private and internal network addresses (SSRF protection), and cap pages and file sizes.

**Pipeline**
1. **Ingest** all sources and store them as project sources.
2. **Brand kit extraction:** name, logo, color palette, fonts, tone, tagline, imagery style. Shown to the user as an editable brand kit. Use sensible defaults, with a clear warning, when something is missing.
3. **Business understanding (Claude):** what the business offers, who it is for, key selling points, proof points, and tone.
4. **Script and storyboard (Claude):** hook, key points, call to action, and a scene-by-scene plan with timing and on-screen text. **Fact grounding rule:** every factual claim (price, statistic, feature, promise) must come from the sources or the user's prompt, and each one is tagged with its source. Claude must not invent numbers, claims, or testimonials. The user approves the script and storyboard before rendering.
5. **Composition JSON:** the storyboard becomes a structured composition (Section 5.11), validated against a schema.
6. **Asset preparation:** photo cleanup, text-to-speech voiceover (pluggable provider, multiple languages and voices, or the user's own voiceover), music, and sound effects.
7. **Render:** a deterministic frame-stepped renderer plays the animated templates and FFmpeg encodes the video with the audio.
8. **Self-review:** Claude checks sampled frames for text overflow or clipping, overlaps, low contrast, unsafe margins, and distorted logos, and returns fixes as composition patches. At most 2 iterations.

**Template library**
- Launch with 10 polished templates: kinetic text, product reveal, device mockup (phone or laptop showing the app or site), feature list, animated stats counter, testimonial card, price or offer, logo sting, CTA end card, and footage scene.
- Each template defines a props schema, safe areas, auto text-fit (shrink and line limits), a minimum contrast check, and layouts for 9:16, 1:1, 16:9, and 4:5. All support right-to-left text and long words.
- Templates take colors, fonts, and the logo from the brand kit.

**Editing UI**
- A storyboard view with one card per scene. The user can edit text, swap images, change the template, timing, colors, fonts, and voice, reorder or delete scenes, and regenerate a single scene with a note (for example "make this punchier"). Re-render reuses cached assets.

### 5.11 Composition Schema (Motion Graphics)

The motion graphics equivalent of the EDL. It lives in `/packages/schemas` and is validated the same way (reject and retry on invalid output).

```json
{
  "version": 1,
  "meta": { "title": "", "aspect": "9:16", "duration_s": 30, "fps": 30, "language": "en", "style": "bold_modern", "goal": "promo" },
  "brand": {
    "name": "",
    "logo_asset": "asset_id",
    "colors": { "primary": "#000000", "secondary": "#000000", "accent": "#000000", "background": "#FFFFFF", "text": "#111111" },
    "fonts": { "heading": "", "body": "" },
    "tone": "friendly"
  },
  "scenes": [
    {
      "id": "sc1",
      "template": "kinetic_text",
      "t_in": 0.0,
      "t_out": 3.0,
      "content": { "headline": "", "subtext": "", "assets": ["asset_id"] },
      "claims": [ { "text": "", "source": "website:/pricing" } ],
      "animation": { "in": "slide_up", "out": "fade", "easing": "ease_out" },
      "transition_out": { "type": "wipe", "duration_s": 0.4 },
      "reason": "Hook: states the main benefit"
    }
  ],
  "voiceover": { "enabled": true, "voice": "", "script": [ { "t_in": 0.0, "text": "" } ] },
  "captions": { "enabled": true, "style": "word_pop" },
  "audio": {
    "music": { "enabled": true, "mood": "upbeat", "asset_id": null, "duck_under_voiceover": true },
    "sfx": [],
    "normalize_lufs": -14
  }
}
```

Every scene carries a `reason`, and every factual claim carries a `source`. A scene with `template: "footage"` references a `clip_id` with `src_in` and `src_out` and is processed through the Edit pipeline.

## 6. Non-Functional Requirements
- **Performance:** a 5-minute video produces a first preview in under 5 minutes under normal load. Re-render after a tweak under 60 seconds. A 30-second motion graphics video renders its first preview in under 3 minutes after the script is approved.
- **Reliability:** every pipeline stage is idempotent and resumable. Failed jobs retry with backoff and never lose uploads.
- **Network:** works on slow connections: resumable uploads, proxy-based preview, progressive download.
- **Cost control:** local pruning before AI, low-res frames, batch requests, prompt caching, per-user caps, and a hard per-job cost ceiling with a clear error.
- **Privacy:** uploads are private and encrypted at rest. User can delete a video and all derived data. Auto-delete after N days (default 30). Do not use user content for training.
- **Observability:** structured logs, job traces, per-stage timing and cost, error tracking.
- **Accessibility:** captions on by default for output, keyboard-navigable UI, readable contrast.

## 7. Architecture

**Recommended stack (defaults; deviate only with a reason in `DECISIONS.md`)**
- **Backend/pipeline:** Python 3.11+ with FastAPI. Celery or RQ with Redis for the job queue (or Temporal if durable workflows are needed).
- **Media:** FFmpeg and ffprobe, PySceneDetect, OpenCV, MediaPipe, faster-whisper (or the Whisper API), librosa for beat and loudness analysis.
- **AI:** Claude API (vision plus structured output). Model names go in environment variables (`CLAUDE_MODEL_FAST` for the coarse pass, `CLAUDE_MODEL_SMART` for the story and review passes). Use prompt caching and batching where available.
- **Storage:** S3-compatible object storage (Cloudflare R2 preferred, no egress fees). Postgres for jobs, users, EDLs, usage.
- **Frontend:** Next.js (web app) first. A React Native client comes after the web MVP and reuses the same API.
- **Rendering:** FFmpeg filter graphs generated from the EDL by a `render` module. Use `libass` for caption burn-in (ASS subtitles with animations). Use GPU encoders where available, with a CPU fallback.
- **Infra:** Docker Compose for local development. A single deployable worker image. Config via environment variables.

**Modules**
```
/apps/api            FastAPI app, auth, uploads, job endpoints
/apps/web            Next.js UI (player, EDL timeline, editor)
/packages/pipeline   ingest, signals, transcribe, vision, story, edl, review
/packages/render     EDL -> FFmpeg graph, captions (ASS), effects, color, audio mix
/packages/assets     b-roll, music, sfx providers and license tracking
/packages/schemas    EDL and Composition schemas (single source of truth), API types
/packages/brand      URL, app listing, and file ingestion; brand kit extraction
/packages/motion     template library, frame-stepped composition renderer, voiceover (TTS)
/infra               Docker, compose, CI
/docs                PRD.md, DECISIONS.md, PROMPTS.md
```

## 8. API (v1)
- `POST /uploads` (init), `PATCH /uploads/{id}` (chunk), `POST /uploads/{id}/complete`
- `POST /projects` (create from the start screen: `mode` is `edit` or `motion`, plus prompt, options, and attachments or sources)
- `POST /projects/{id}/clips` (add a clip; rejected if the total raw length would exceed the plan limit), `DELETE /projects/{id}/clips/{clip_id}`, `PUT /projects/{id}/clips/order` (reorder)
- Motion mode: `POST /projects/{id}/sources` (add a URL, app listing link, or file), `GET` and `PUT /projects/{id}/brand-kit`, `GET /projects/{id}/storyboard`, `POST /projects/{id}/storyboard/approve`, `GET` and `PUT /projects/{id}/composition` (validated), `POST /projects/{id}/scenes/{scene_id}/regenerate` (with a user note)
- `GET /projects/{id}` (status, stage, progress)
- `GET /projects/{id}/edl`, `PUT /projects/{id}/edl` (validated)
- `POST /projects/{id}/render` (options: aspect, resolution)
- `GET /projects/{id}/preview` and `/export`
- `GET /projects/{id}/report` (what was cut and why)
- `DELETE /projects/{id}` (removes all derived data)
- Webhooks or SSE for progress.

## 9. Data Model (core tables)
`users`, `projects`, `project_clips` (ordered clips per project; `projects` has a `mode` column), `project_sources` (URLs, app listings, files for motion mode), `brand_kits`, `compositions` (versioned, motion mode), `templates`, `uploads`, `analysis_artifacts` (per clip: signals, transcript, timeline; per project: story), `edls` (versioned), `renders`, `assets` (b-roll, music, sfx with license info), `usage_events` (per-stage cost), `credits_ledger`.

## 10. AI Prompting Requirements
- Keep all prompts in `/docs/PROMPTS.md` and in versioned files, never inline.
- Every Claude call must return JSON matching a schema. Validate, retry once with the error message, then fail the stage cleanly.
- Frames are sent with timestamps in the prompt so Claude can reference exact times.
- Prompts must instruct Claude to: prefer preserving meaning over aggressive cutting, never cut mid-sentence or mid-action, state a `reason` for every decision, and respect the chosen preset and target length.
- Provide Claude the local signals (motion, silence, beats) as context so it does not re-derive them from frames.
- Build an eval set (see Section 12) and re-run it whenever prompts or models change.
- Motion mode prompts (business understanding, storyboard, composition, review) must use only facts found in the provided sources or the user's prompt, tag each claim with its source, omit anything they cannot source, and respect the brand kit and chosen template constraints (text length limits, safe areas).

## 11. Safety, Legal, Content
- Use only licensed b-roll and audio. Store the source, license, and attribution for every asset used. Attribution is shown in export metadata where required.
- Do not generate or retain content about identifying private individuals. Face detection is used only for framing, not identification.
- Content moderation check on uploads (basic NSFW and abuse screening) before analysis.
- Motion mode: before processing a website, app listing, or brand assets, require the user to confirm they own or are authorized to use that brand and its assets, and store that confirmation. Do not create content that impersonates another brand or person.
- Motion mode: never invent claims, prices, reviews, or testimonials. Use testimonials only when they appear in the provided sources; otherwise leave them out.
- Track licenses for fonts and any stock media used in motion graphics.
- Terms and privacy copy is placeholder text only; flag it for legal review.

## 12. Quality and Evaluation
- Create `/evals` with 20+ sample videos covering: silent action, talking head, tutorial, product demo, vlog, noisy audio, vertical and horizontal.
- Metrics: percentage of dead time removed, rate of mid-sentence or mid-action cuts (target: ~0), caption word accuracy, human rating of overall edit quality (1 to 5), cost per minute, time per minute.
- Automated checks: EDL schema validity, no gaps or overlaps, audio and video stay in sync, loudness within target, output duration within the target range.
- Regression suite runs in CI on a few short clips.
- Motion mode eval set: 20+ sample businesses (websites, app listings, product photo sets, brand files), including messy sources such as low-resolution photos and cluttered sites. Metrics: text overflow or clipping rate (target: 0), brand color and logo fidelity, share of factual claims traceable to a source (target: 100%), and human rating of overall quality (1 to 5).

## 13. Success Metrics
- At least 70% of generated edits are accepted with little or no change.
- Median processing time under 1x the video length.
- Cost per processed minute below the target set in config (start with a $0.15 ceiling and tune).
- Export completion rate above 60% of started projects.
- Motion mode: at least 60% of storyboards approved without a full rewrite, and zero unsourced factual claims in approved scripts.

## 14. Build Order (Milestones)

Complete each one, run its tests, and summarize before continuing.

1. **M0: Skeleton.** Repo layout, Docker Compose (api, worker, redis, postgres, minio/R2), EDL schema package with tests, CI.
2. **M1: Ingest and signals.** Multi-clip project model, upload, ffprobe, proxy creation, total-length limit enforcement, per-clip local signal pass, `signals.json`. CLI: `autoedit analyze clip1.mp4 clip2.mp4 ...`.
3. **M2: Transcription.** Whisper with word timestamps, filler and silence detection.
4. **M3: Vision understanding.** Per-clip coarse pass and `timeline.json`, then a project-level story pass across all clips, `story.json`. Check on silent clips first, then on multi-clip projects.
5. **M4: EDL and basic render.** Claude produces a valid EDL (cuts only). Render with FFmpeg. Fine-pass boundary refinement. CLI: `autoedit edit video.mp4 -o out.mp4`.
6. **M5: Captions and audio.** ASS word-by-word captions, denoise, loudness, music ducking.
7. **M6: Effects, transitions, reframing, color grade.** Event-driven effects, subject-tracking crop, auto-correct plus presets.
8. **M7: B-roll and sound effects.** Provider integrations, license tracking, placement logic.
9. **M8: Self-review loop.** Output frame review, EDL patching, max 2 iterations.
10. **M9: Web app.** Start screen (chat box with mode chooser; Motion mode shown as "coming soon" until M16), multi-clip upload UI (add, remove, reorder, total-length meter), progress, preview player, EDL timeline editor, re-render, export.
11. **M10: Accounts, credits, payments.** Paystack/Flutterwave, usage ledger, caps.
12. **M11: Hardening.** Evals, load test, retries, privacy deletion, monitoring, docs.
13. **M12: Motion brand ingestion.** Source intake (URL, app listing, photos, files), safe crawler (SSRF protection, robots.txt, limits), brand kit extraction, photo cleanup and background removal, editable brand kit. CLI: `autoedit brand https://example.com`.
14. **M13: Motion engine and templates.** Composition schema, frame-stepped renderer, first 3 templates (kinetic text, product reveal, CTA end card), text-fit and safe-area rules, multi-aspect output.
15. **M14: Storyboard and script.** Business understanding, script and storyboard with fact grounding and source tags, approval gate, Composition JSON generation. CLI: `autoedit motion --brand brand.json -o out.mp4`.
16. **M15: Motion audio and polish.** TTS voiceover, music, sound effects, captions synced to the voiceover, remaining templates (up to 10), motion self-review loop.
17. **M16: Motion mode in the web app.** Enable the mode on the start screen, source intake UI with chips, brand kit and storyboard editors, single-scene regenerate, re-render, export, billing.
18. **M17: Crossover.** Motion intros, outros, and callouts inside Edit mode. Footage scenes inside Motion mode.
19. **M18 (later): React Native client.**

## 15. Definition of Done (per milestone)
- Code is typed, linted, and tested. CI passes.
- A runnable demo command or screen exists and is documented in the README.
- New decisions are logged in `DECISIONS.md`.
- Any cost or latency regression is reported.

## 16. Open Questions (use these defaults unless I say otherwise)
- Platform: web first, mobile later.
- Hosting: any provider; keep it portable with Docker.
- Claude models: configured via environment variables.
- Languages: multilingual from day one (all Whisper-supported languages for transcription and captions, localization-ready UI).
- Free-tier limits: 3 min total raw footage per project (summed across all clips), 3 projects per month, watermark on export.
- Motion free tier: 3 motion projects per month, up to 30 seconds each, watermark on export.
- Motion render engine: a frame-stepped HTML renderer (headless Chromium plus FFmpeg) by default. Use Remotion only if its license terms are reviewed and acceptable for this product, and record the choice in `DECISIONS.md`.
- Text-to-speech provider: pluggable; choose one with good multilingual support and record it in `DECISIONS.md`.

## 17. Start Now
Begin with **M0 and M1**. When M1 is done, show me the `signals.json` output for a sample clip and the commands to run it, then wait for my go-ahead before M2.
