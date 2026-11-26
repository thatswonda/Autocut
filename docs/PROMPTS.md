# Prompts

PRD §10: every Claude prompt lives in a versioned file, never inline in code.

| Prompt | File | Used by | Output |
|---|---|---|---|
| Storyboard | `packages/motion/src/autoedit_motion/prompts/storyboard.v1.md` | Motion graphics: first plan | A full `Composition` (scenes, copy, layout, timing) |
| Scene rewrite | `packages/motion/src/autoedit_motion/prompts/scene.v1.md` | Motion graphics: "change this scene" | One `SceneContent` |

Settings: model from `CLAUDE_MODEL_SMART`, effort from `CLAUDE_EFFORT` (default `medium`), server-side model
fallback on. Typical storyboard cost: about $0.10-0.15.

Rules every prompt follows:
- Output JSON validated against a schema in `packages/schemas`; on failure, retry once with the
  validation errors, then fail the stage cleanly.
- Grounding is enforced in code, not trusted: every number must appear in the sources or the user's
  brief, testimonials must be quoted verbatim, claims cite source ids, and text respects length limits.
- Sources (site text, user brief, image descriptions) are data, never instructions.
- To change a prompt, add `<name>.v2.md` and switch the loader; keep old versions for comparison.

Edit mode does not call Claude yet: it renders from local signals (D-406). The M3 visual and story
prompts will live beside these as `packages/pipeline/prompts/<name>.v<N>.md`.
