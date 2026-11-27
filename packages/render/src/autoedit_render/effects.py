"""Camera moves, effects and cut transitions for one segment, as FFmpeg filter chains.

Everything works on a single segment at the output size, so segments never overlap and audio stays in
sync. A transition between two segments is made of an "out" half at the end of the first and an "in"
half at the start of the second (how short-form editors build zoom, whip and glitch transitions).

Moves (whole segment): ``zoom_in``, ``zoom_out`` (slow push), ``pan_left``/``pan_right``, ``shake``,
``punch`` (static tighter framing). Edges: ``zoom`` (zoom-blur), ``whip`` (fast slide with motion blur),
``flash`` (white flash), ``glitch`` (RGB split, noise, jitter), ``fade`` (dip to black).
Momentary effects: ``glitch`` and ``flash`` at a time inside the segment, ``shake`` hit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EDGE_S = 0.28  # length of each half of a transition
MOVES = {"none", "zoom_in", "zoom_out", "pan_left", "pan_right", "shake", "punch"}
EDGES = {"cut", "zoom", "whip", "flash", "glitch", "fade"}


@dataclass
class PieceFx:
    move: str = "none"
    strength: float = 1.0  # 0.5 subtle .. 1.5 strong
    edge_in: str = "cut"
    edge_out: str = "cut"
    hits: list[tuple[float, str]] = field(
        default_factory=list
    )  # (t inside the piece, "glitch"|"flash"|"shake")

    @property
    def needs_margin(self) -> bool:
        return (self.move in ("pan_left", "pan_right", "shake") or self.edge_in == "whip"
                or self.edge_out == "whip" or any(h[1] == "shake" for h in self.hits))  # fmt: skip


def _f(x: float) -> str:
    return f"{x:.4f}"


def motion_chain(fx: PieceFx, dur: float, w: int, h: int) -> str:
    """Filters that move the camera. Input: a frame exactly ``w``x``h``. Output: same size."""
    d = max(dur, 0.1)
    s = max(0.3, min(1.6, fx.strength))
    e = EDGE_S
    # Scale factor over time: a slow move plus zoom-blur edges.
    zoom_terms = ["1"]
    if fx.move == "zoom_in":
        zoom_terms.append(f"{_f(0.10 * s)}*(t/{_f(d)})")
    elif fx.move == "zoom_out":
        zoom_terms.append(f"{_f(0.10 * s)}*(1-t/{_f(d)})")
    # "punch" is done by the reframe (a tighter crop from the full-resolution source), not here.
    if fx.edge_in == "zoom":
        zoom_terms.append(f"0.35*pow(max(0,1-t/{_f(e)}),2)")
    if fx.edge_out == "zoom":
        zoom_terms.append(f"0.35*pow(max(0,(t-{_f(d - e)})/{_f(e)}),2)")
    margin = 1.12 if fx.needs_margin else 1.0
    zoom = "+".join(zoom_terms)
    dynamic = len(zoom_terms) > 1
    # Horizontal/vertical offsets in units of the free margin (-1..1).
    xs: list[str] = []
    ys: list[str] = []
    if fx.move == "pan_left":
        xs.append(f"(1-2*t/{_f(d)})")
    elif fx.move == "pan_right":
        xs.append(f"(2*t/{_f(d)}-1)")
    if fx.move == "shake":
        xs.append(f"{_f(0.5 * s)}*sin(t*23)*sin(t*7.3)")
        ys.append(f"{_f(0.4 * s)}*sin(t*19+1.3)*sin(t*5.1)")
    for t0, kind in fx.hits:
        if kind == "shake":
            xs.append(
                f"if(between(t,{_f(t0)},{_f(t0 + 0.35)}),0.9*sin((t-{_f(t0)})*60)*exp(-(t-{_f(t0)})*9),0)"
            )
            ys.append(
                f"if(between(t,{_f(t0)},{_f(t0 + 0.35)}),0.7*cos((t-{_f(t0)})*53)*exp(-(t-{_f(t0)})*9),0)"
            )
    if fx.edge_in == "whip":
        xs.append(f"if(lt(t,{_f(e)}),-1*pow(1-t/{_f(e)},2),0)")
    if fx.edge_out == "whip":
        xs.append(f"if(gt(t,{_f(d - e)}),pow((t-{_f(d - e)})/{_f(e)},2),0)")
    parts: list[str] = []
    if dynamic or margin > 1:
        if dynamic:
            parts.append(f"scale=w='trunc({w}*{_f(margin)}*({zoom})/2)*2':h=-2:eval=frame:flags=bicubic")
        else:
            parts.append(f"scale=w={round(w * margin) // 2 * 2}:h=-2:flags=lanczos")
        x = f"(iw-ow)/2*(1+({'+'.join(xs)}))" if xs else "(iw-ow)/2"
        y = f"(ih-oh)/2*(1+({'+'.join(ys)}))" if ys else "(ih-oh)/2"
        parts.append(f"crop={w}:{h}:x='max(0,min(iw-ow,{x}))':y='max(0,min(ih-oh,{y}))'")
    # Motion blur and looks on the edges.
    blur = []
    if fx.edge_in in ("zoom", "whip"):
        blur.append(f"lt(t,{_f(e * 0.8)})")
    if fx.edge_out in ("zoom", "whip"):
        blur.append(f"gt(t,{_f(d - e * 0.8)})")
    if blur:
        en = "+".join(blur)
        horizontal = "whip" in (fx.edge_in, fx.edge_out)
        size = max(6, w // 40)
        parts.append(f"avgblur=sizeX={size}:sizeY={1 if horizontal else size}:enable='{en}'")
    parts += _edge_looks(fx, d, w)
    return ",".join(parts)


def _glitch(a: float, b: float, w: int) -> list[str]:
    shift = max(6, w // 70)
    en = f"between(t,{_f(a)},{_f(b)})"
    return [
        f"rgbashift=rh={-shift}:bh={shift}:gv={shift // 2}:enable='{en}'",
        f"noise=alls=38:allf=t:enable='{en}'",
        f"eq=contrast=1.25:saturation=1.4:enable='{en}'",
    ]


def _edge_looks(fx: PieceFx, d: float, w: int) -> list[str]:
    e = EDGE_S
    out: list[str] = []
    if fx.edge_in == "flash":
        out.append(f"fade=t=in:st=0:d={_f(e * 0.8)}:color=white")
    if fx.edge_out == "flash":
        out.append(f"fade=t=out:st={_f(d - e * 0.5)}:d={_f(e * 0.5)}:color=white")
    if fx.edge_in == "fade":
        out.append(f"fade=t=in:st=0:d={_f(e)}")
    if fx.edge_out == "fade":
        out.append(f"fade=t=out:st={_f(d - e)}:d={_f(e)}")
    if fx.edge_in == "glitch":
        out += _glitch(0, e * 0.9, w)
    if fx.edge_out == "glitch":
        out += _glitch(d - e * 0.9, d, w)
    for t0, kind in fx.hits:
        if kind == "glitch":
            out += _glitch(t0, t0 + 0.22, w)
        elif kind == "flash":
            out.append(f"eq=brightness=0.55:contrast=0.7:enable='between(t,{_f(t0)},{_f(t0 + 0.06)})'")
            out.append(f"eq=brightness=0.25:enable='between(t,{_f(t0 + 0.06)},{_f(t0 + 0.14)})'")
    return out
