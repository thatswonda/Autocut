# Prompts

PRD §10: every Claude prompt lives here and in versioned files, never inline in code.

No prompts exist yet. Stage A (M1) is entirely local and costs nothing in AI. The first prompts arrive
with **M3** (coarse visual pass, story pass), versioned as `packages/pipeline/prompts/<name>.v<N>.md`.

Rules every prompt will follow:
- Output JSON validated against a schema in `packages/schemas`; on failure, retry once with
  `SchemaValidationError.feedback`, then fail the stage cleanly.
- Frames carry timestamps; local signals (`signals.json`) are passed as context so Claude does not
  re-derive motion, silence, or beats.
- Prefer meaning over aggressive cutting; never cut mid-sentence or mid-action; give a `reason` for
  every decision; respect the preset and target length.
- Dead zones graded `static_subject_hold` are sampled sparsely, not skipped; `static_empty` and
  `black_frames` are skipped.
