# packages/render — EDL → FFmpeg (M4–M6)

Deterministic renderer: turns a validated `EDL` (see `packages/schemas`) into an FFmpeg filter graph.
Captions via libass (ASS), effects, color, audio mix. GPU encoders when available, CPU fallback.
Not started: lands in **M4** (cuts), **M5** (captions/audio), **M6** (effects, reframing, grade).
