"""Build the HTML page for a Composition: one absolutely positioned layer per scene, all animation
expressed as Web Animations that the runtime pauses and seeks frame by frame (deterministic output)."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoedit_schemas.composition import Composition, Scene

from .styles import Surfaces, contrast, get_style, resolve_surface

FONTS_DIR = Path(__file__).parent / "fonts"
RUNTIME_JS = (Path(__file__).parent / "runtime.js").read_text()

FONT_FAMILIES = {
    "inter": "Inter",
    "space-grotesk": "Space Grotesk",
    "poppins": "Poppins",
    "playfair-display": "Playfair Display",
    "bebas-neue": "Bebas Neue",
    "dm-serif-display": "DM Serif Display",
    "montserrat": "Montserrat",
    "noto-sans-arabic": "Noto Sans Arabic",
}
RTL_LANGS = {"ar", "he", "fa", "ur", "ps", "yi"}

ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "16:9": (16, 9), "4:5": (4, 5)}


def output_size(aspect: str, short_edge: int = 720) -> tuple[int, int]:
    """Pixel size for an aspect ratio with the given short edge (even numbers for H.264)."""
    a, b = ASPECTS.get(aspect, (9, 16))
    if a <= b:
        w, h = short_edge, round(short_edge * b / a)
    else:
        w, h = round(short_edge * a / b), short_edge
    return w - w % 2, h - h % 2


@dataclass
class PageSpec:
    duration: float
    scenes: list[dict[str, Any]] = field(default_factory=list)
    anims: list[dict[str, Any]] = field(default_factory=list)
    counters: list[dict[str, Any]] = field(default_factory=list)
    footage: list[dict[str, Any]] = field(default_factory=list)
    overlays: list[dict[str, Any]] = field(default_factory=list)


class _Rand:
    """Tiny deterministic generator so particle layouts are identical on every render."""

    def __init__(self, seed: int):
        self.x = seed % 2147483647 or 1

    def uniform(self, a: float, b: float) -> float:
        self.x = self.x * 48271 % 2147483647
        return a + (b - a) * self.x / 2147483647


@dataclass(frozen=True)
class FootageFrames:
    """Pre-extracted frames for one footage scene: ``{prefix}{index:05d}.jpg``, ``count`` of them."""

    prefix: str
    count: int
    fps: int


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def _font_faces() -> str:
    rules = []
    for f in sorted(FONTS_DIR.glob("*.woff2")):
        stem, weight = f.stem.rsplit("-", 1)
        family = FONT_FAMILIES.get(stem)
        if family:
            rules.append(
                f"@font-face{{font-family:'{family}';src:url('{f.resolve().as_uri()}') format('woff2');"
                f"font-weight:{weight};font-style:normal;font-display:block}}"
            )
    return "\n".join(rules)


def _stack(family: str) -> str:
    return f"'{family}','Noto Sans Arabic','Inter',system-ui,sans-serif"


_NUM = re.compile(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(\D*)$")


def _counter(value: str) -> tuple[str, float, int, str] | None:
    """Split '10,000+' into ('', 10000, 0, '+') so it can count up. None when it should not animate."""
    m = _NUM.match(value.strip())
    if not m:
        return None
    prefix, num, suffix = m.groups()
    if any(ch.isdigit() for ch in suffix) or suffix.startswith(("/", ":", "-")):
        return None
    decimals = len(num.split(".")[1]) if "." in num else 0
    return prefix, float(num.replace(",", "")), decimals, suffix


class _Builder:
    def __init__(self, comp: Composition, assets: dict[str, str],
                 footage: dict[str, FootageFrames] | None = None):  # fmt: skip
        self.comp = comp
        self.assets = assets
        self.footage = footage or {}
        self.style = get_style(comp.meta.style)
        self.spec = PageSpec(duration=comp.meta.duration_s)
        self.colors = comp.brand.colors.model_dump()
        self.centered = self.style.key in ("clean_minimal", "luxury", "warm_friendly")

    # -------------------------------------------------------------------------------- animation
    def anim(self, sel: str, preset: str, start: float, dur: float, *, stagger: float = 0.0,
             easing: str | None = None, iterations: float = 1) -> None:  # fmt: skip
        self.spec.anims.append(
            {
                "sel": sel,
                "preset": preset,
                "start": round(max(0.0, start), 4),
                "dur": round(max(0.05, dur), 4),
                "stagger": stagger,
                "easing": easing or self.style.easing,
                "iter": iterations,
            }
        )

    # ---------------------------------------------------------------------------------- pieces
    def img(self, asset_id: str | None, cls: str) -> str:
        if not asset_id or asset_id not in self.assets:
            return ""
        return f'<img class="{cls}" src="{_e(self.assets[asset_id])}" alt="">'

    def words(self, text: str) -> str:
        return " ".join(
            f'<span class="w"><span class="wi">{_e(w)}</span></span>' for w in (text or "").split()
        )

    def headline(self, sid: str, text: str, max_u: float, min_u: float = 4.0, cls: str = "",
                 maxh: float = 60) -> str:  # fmt: skip
        if not text:
            return ""
        inner = self.words(text)
        return (
            f'<h1 class="headline fit {cls}" dir="auto" data-max="{max_u}" data-min="{min_u}" '
            f'data-maxh="{maxh}" data-label="{sid} headline">{inner}</h1>'
        )

    def text(self, sid: str, cls: str, text: str, max_u: float, min_u: float, tag: str = "p",
             maxh: float = 28) -> str:  # fmt: skip
        if not text:
            return ""
        return (
            f'<{tag} class="{cls} fit" dir="auto" data-max="{max_u}" data-min="{min_u}" '
            f'data-maxh="{maxh}" data-label="{sid} {cls}">{_e(text)}</{tag}>'
        )

    def kicker(self, text: str) -> str:
        return f'<div class="kicker" dir="auto">{_e(text)}</div>' if text else ""

    def logo(self, cls: str = "logo") -> str:
        logo_id = self.comp.brand.logo_asset
        if logo_id and logo_id in self.assets:
            return f'<div class="{cls}"><img src="{_e(self.assets[logo_id])}" alt=""></div>'
        name = self.comp.brand.name
        return f'<div class="{cls} logo-text" dir="auto">{_e(name)}</div>' if name else ""

    # ------------------------------------------------------------------------------------ scenes
    def scene(self, sc: Scene, index: int, prev: Scene | None, surf: Surfaces) -> str:
        sid = sc.id
        t0, dur = sc.t_in, sc.t_out - sc.t_in
        k = min(1.0, dur / 3.0)  # compress entrance offsets in short scenes
        c = sc.content
        tpl = sc.template
        body = getattr(self, f"tpl_{tpl}")(sc, t0, k)

        # Shared entrances (selectors only match when the template rendered that element).
        st = self.style
        ease_out = "cubic-bezier(.16,1,.3,1)"
        self.anim(f"#{sid} .kicker", "track_in", t0 + 0.05 * k, 0.8, easing=ease_out)
        word_anim = {"pop_words": "pop", "mask_reveal": "word_up", "blur_in": "word_blur",
                     "slide_left": "word_slide"}.get(st.headline_anim, "word_up")  # fmt: skip
        target = f"#{sid} .headline .w" if word_anim == "pop" else f"#{sid} .headline .wi"
        self.anim(target, word_anim, t0 + 0.14 * k, 0.75, stagger=0.065, easing=ease_out)
        self.anim(f"#{sid} .bar", "bar_grow", t0 + 0.45 * k, 0.7, easing=ease_out)
        self.anim(f"#{sid} .subtext", "soft_up", t0 + 0.5 * k, 0.8, easing=ease_out)
        # Camera: a slow push on the content and a counter-drift on the background (parallax).
        self.anim(f"#{sid} > .content", "cam_push", t0 - 0.4, dur + 0.8, easing="linear")
        self.anim(f"#{sid} > .bg", "cam_drift", t0 - 0.4, dur + 0.8, easing="linear")

        # Background ambience and light.
        bg_html = self.background(sid, t0, dur) + self.fx_layers(sc, index, t0, dur)
        del c, prev
        # Logos sit on a light plate when the scene color would swallow them.
        plate = self.colors.get("background", "#ffffff")
        needs_plate = contrast(surf.bg, plate) > 1.6
        style_vars = (
            f"--bg:{surf.bg};--bg2:{surf.bg2};--ink:{surf.ink};--muted:{surf.muted};"
            f"--accent:{surf.accent};--accent-ink:{surf.accent_ink};"
            f"--plate:{plate if needs_plate else 'transparent'};--plate-pad:{1.4 if needs_plate else 0}"
        )
        align = "center" if self.centered else "left"
        return (
            f'<section class="scene tpl-{tpl} align-{align}" id="{sid}" style="{style_vars}">'
            f'{bg_html}<div class="content">{body}</div></section>'
        )

    def fx_layers(self, sc: Scene, index: int, t0: float, dur: float) -> str:
        """Light, particles and accents that make a scene feel shot rather than laid out."""
        sid = sc.id
        if sc.template == "footage":
            self.anim(f"#{sid} .streak", "streak", t0 + 0.25, 1.1, easing="cubic-bezier(.4,0,.2,1)")
            return '<div class="fx"><i class="streak"></i></div>'
        calm = self.style.key in ("clean_minimal", "luxury", "editorial")
        n = 7 if calm else 14
        rnd = _Rand(index * 7919 + 17)
        dots = []
        for _ in range(n):
            size = rnd.uniform(0.5, 1.6 if calm else 2.4)
            dots.append(f'<i class="dot" style="left:{rnd.uniform(2, 98):.1f}%;top:{rnd.uniform(5, 98):.1f}%;'
                        f'width:calc(var(--u)*{size:.2f});height:calc(var(--u)*{size:.2f});'
                        f'opacity:{rnd.uniform(0.25, 0.75):.2f}"></i>')  # fmt: skip
        self.anim(f"#{sid} .dot", "float_up", t0 - 0.8, dur + 1.4, stagger=0.0, easing="linear")
        self.anim(f"#{sid} .glow", "glow_drift", t0 - 0.5, dur + 1.0, easing="ease-in-out")
        self.anim(f"#{sid} .streak", "streak", t0 + 0.3, 1.2, easing="cubic-bezier(.4,0,.2,1)")
        self.anim(
            f"#{sid} .corner", "corner_draw", t0 + 0.35, 0.7, stagger=0.12, easing="cubic-bezier(.16,1,.3,1)"
        )
        side = "l" if index % 2 else "r"
        corners = "" if calm else '<i class="corner tl"></i><i class="corner br"></i>'
        return f'<div class="fx"><i class="glow g{side}"></i><i class="streak"></i>{"".join(dots)}{corners}</div>'

    def background(self, sid: str, t0: float, dur: float) -> str:
        kind = self.style.background
        if kind == "shapes":
            self.anim(f"#{sid} .shape.a", "drift_a", t0 - 0.6, dur + 1.2, easing="linear")
            self.anim(f"#{sid} .shape.b", "drift_b", t0 - 0.6, dur + 1.2, easing="linear")
            return '<div class="bg bg-shapes"><i class="shape a"></i><i class="shape b"></i></div>'
        if kind == "grid":
            self.anim(f"#{sid} .bg-grid", "grid_pan", t0 - 0.6, dur + 1.2, easing="linear")
            return '<div class="bg bg-grid"></div>'
        return f'<div class="bg bg-{kind}"></div>'

    # Each template returns inner HTML and registers its own element animations.
    def tpl_kinetic_text(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        return (
            f"{self.kicker(c.kicker)}{self.headline(sc.id, c.headline, 15, 5)}"
            f'<div class="bar"></div>{self.text(sc.id, "subtext", c.subtext, 4.8, 3)}'
        )

    def tpl_footage(self, sc: Scene, t0: float, k: float) -> str:
        """The business's own clip, full bleed, with copy over a soft scrim. While rendering, the frame
        for each moment comes from ``FootageFrames``; in previews the clip's poster frame stands in."""
        c = sc.content
        frames = self.footage.get(sc.id)
        poster = self.assets.get(sc.clip_id or "", "")
        src = f"{frames.prefix}00000.jpg" if frames else poster
        if frames:
            self.spec.footage.append({"id": sc.id, "from": sc.t_in, "to": sc.t_out, "prefix": frames.prefix,
                                      "count": frames.count, "fps": frames.fps})  # fmt: skip
        self.anim(f"#{sc.id} .footage", "slow_zoom", t0, sc.t_out - t0, easing="linear")
        img = f'<img class="footage" data-footage="{sc.id}" src="{_e(src)}" alt="">' if src else ""
        has_copy = bool(c.kicker or c.headline or c.subtext)
        return (
            f'<div class="footage-wrap">{img}</div>{"<i class=scrim></i>" if has_copy else ""}'
            f'<div class="copy">{self.kicker(c.kicker)}{self.headline(sc.id, c.headline, 10, 4.5, maxh=24)}'
            f"{self.text(sc.id, 'subtext', c.subtext, 4.4, 3)}</div>"
        )

    def tpl_product_reveal(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        aid = c.assets[0] if c.assets else None
        image = self.img(aid, "product")
        # Photos with a background become a tilted card; transparent PNG cut-outs float freely.
        is_card = bool(aid) and not str(self.assets.get(aid or "", "")).lower().endswith(".png")
        d = sc.t_out - t0
        self.anim(f"#{sc.id} .burst", "spin_in", t0, d, easing="linear")
        self.anim(f"#{sc.id} .plate", "pop", t0 + 0.0, 0.7)
        self.anim(f"#{sc.id} .media", "tilt_in", t0 + 0.1 * k, 1.1, easing="cubic-bezier(.16,1,.3,1)")
        self.anim(
            f"#{sc.id} .floaty",
            "bob",
            t0 + 1.0,
            2.6,
            iterations=max(1.0, (d - 1.0) / 2.6),
            easing="ease-in-out",
        )
        self.anim(f"#{sc.id} .shine", "shine", t0 + 0.95 * k + 0.2, 1.0, easing="cubic-bezier(.4,0,.2,1)")
        media = (
            f'<div class="media-wrap"><i class="burst"></i><i class="plate"></i>'
            f'<div class="media"><div class="floaty{" card" if is_card else ""}">{image}<i class="shine"></i></div>'
            f"{'<i class=reflect></i>' if is_card else ''}</div></div>"
            if image
            else ""
        )
        return (
            f'{media}<div class="copy">{self.kicker(c.kicker)}{self.headline(sc.id, c.headline, 10, 4.5, maxh=26)}'
            f"{self.text(sc.id, 'subtext', c.subtext, 4.4, 3)}</div>"
        )

    def tpl_device_mockup(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        shot = self.img(c.assets[0] if c.assets else None, "screen")
        self.anim(f"#{sc.id} .phone", "phone_in", t0 + 0.05 * k, 1.1, easing="cubic-bezier(.16,1,.3,1)")
        self.anim(
            f"#{sc.id} .screen", "scroll", t0 + 0.9, max(0.5, sc.t_out - t0 - 1.2), easing="ease-in-out"
        )
        return (
            f'<div class="copy">{self.kicker(c.kicker)}{self.headline(sc.id, c.headline, 8.5, 4, maxh=24)}'
            f"{self.text(sc.id, 'subtext', c.subtext, 4.2, 3)}</div>"
            f'<div class="phone"><div class="notch"></div><div class="glass">{shot}</div></div>'
        )

    def tpl_feature_list(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        items = "".join(
            f'<li><span class="tick"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.2 4.2L19 7" fill="none" '
            f'stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg></span>'
            f'<span class="it fit" dir="auto" data-max="5" data-min="3" data-maxh="13" data-label="{sc.id} item">{_e(it)}</span></li>'
            for it in c.items[:5]
        )
        self.anim(
            f"#{sc.id} .items li", self.style.item_anim, t0 + 0.55 * k, 0.55, stagger=0.22 * max(k, 0.6)
        )
        return (
            f"{self.kicker(c.kicker)}{self.headline(sc.id, c.headline, 9, 4, maxh=24)}"
            f'<ul class="items">{items}</ul>'
        )

    def tpl_stats_counter(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        cells = []
        for i, stat in enumerate(c.stats[:3]):
            parsed = _counter(stat.value)
            vid = f"{sc.id}-stat{i}"
            if parsed:
                prefix, num, dec, suffix = parsed
                self.spec.counters.append(
                    {
                        "id": vid,
                        "to": num,
                        "dec": dec,
                        "prefix": prefix,
                        "suffix": suffix,
                        "start": t0 + 0.35 + 0.2 * i,
                        "dur": 1.4,
                    }
                )
            cells.append(
                f'<div class="stat"><div class="value fit" id="{vid}" data-max="15" data-min="6" '
                f'data-maxh="17" data-label="{sc.id} stat">{_e(stat.value)}</div>'
                f'<div class="label" dir="auto">{_e(stat.label)}</div></div>'
            )
        self.anim(f"#{sc.id} .stat", "pop", t0 + 0.3 * k, 0.6, stagger=0.2)
        return (
            f"{self.kicker(c.kicker)}{self.headline(sc.id, c.headline, 8, 4, maxh=20)}"
            f'<div class="stats n{len(cells)}">{"".join(cells)}</div>'
        )

    def tpl_testimonial_card(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        self.anim(f"#{sc.id} .card", "rise", t0 + 0.05 * k, 0.8)
        self.anim(f"#{sc.id} .qmark", "pop", t0 + 0.25 * k, 0.6)
        return (
            f'<div class="card"><div class="qmark">&ldquo;</div>'
            f"{self.headline(sc.id, c.headline, 7, 3.6, cls='quote', maxh=50)}"
            f"{self.text(sc.id, 'subtext', c.subtext, 3.8, 2.8)}</div>"
        )

    def tpl_price_offer(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        self.anim(f"#{sc.id} .price", "pop", t0 + 0.4 * k, 0.7)
        self.anim(f"#{sc.id} .chips span", "fade_up", t0 + 0.8 * k, 0.5, stagger=0.12)
        chips = "".join(f'<span dir="auto">{_e(it)}</span>' for it in c.items[:3])
        price = (
            f'<div class="price fit" dir="auto" data-max="24" data-min="9" data-maxh="26" data-label="{sc.id} price">{_e(c.price)}</div>'
            if c.price
            else ""
        )
        return (
            f"{self.kicker(c.kicker)}{self.headline(sc.id, c.headline, 8, 4, maxh=20)}{price}"
            f"{self.text(sc.id, 'subtext', c.subtext, 4.2, 3)}"
            f"{f'<div class=chips>{chips}</div>' if chips else ''}"
        )

    def tpl_logo_sting(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        self.anim(f"#{sc.id} .ring", "ring", t0 + 0.1, 1.1, easing="cubic-bezier(.2,.7,.2,1)")
        self.anim(f"#{sc.id} .ring.r2", "ring", t0 + 0.3, 1.2, easing="cubic-bezier(.2,.7,.2,1)")
        self.anim(f"#{sc.id} .logo-hero", "blur_in", t0 + 0.15, 0.9)
        tagline = c.headline or c.subtext
        return (
            f'<div class="sting"><i class="ring"></i><i class="ring r2"></i>{self.logo("logo-hero")}</div>'
            f"{self.text(sc.id, 'subtext', tagline, 5, 3)}"
        )

    def tpl_cta_end_card(self, sc: Scene, t0: float, k: float) -> str:
        c = sc.content
        self.anim(f"#{sc.id} .cta", "pop", t0 + 0.55 * k, 0.6)
        self.anim(f"#{sc.id} .cta", "pulse", t0 + 1.4, 1.2, iterations=max(1, (sc.t_out - t0 - 1.4) / 1.2))
        self.anim(f"#{sc.id} .logo-small", "fade_up", t0 + 0.1, 0.6)
        self.anim(
            f"#{sc.id} .cta .shine", "shine", t0 + 1.0, 0.9, iterations=2, easing="cubic-bezier(.4,0,.2,1)"
        )
        cta = (f'<div class="cta" dir="auto"><span>{_e(c.cta)}</span><span class="arrow">&rarr;</span>'
               f'<i class="shine"></i></div>' if c.cta else "")  # fmt: skip
        return (
            f"{self.logo('logo-small')}{self.headline(sc.id, c.headline, 11, 5, maxh=36)}{cta}"
            f"{self.text(sc.id, 'subtext', c.subtext, 4, 2.8)}"
        )

    # ------------------------------------------------------------------------------------- page
    def build(self) -> str:
        scenes = sorted(self.comp.scenes, key=lambda s: s.t_in)
        layers = []
        overlays: list[str] = []
        wipes: list[dict[str, Any]] = []
        n = len(scenes)
        for i, sc in enumerate(scenes):
            prev = scenes[i - 1] if i else None
            surf = resolve_surface(self.style.surfaces[i % len(self.style.surfaces)], self.colors)
            layers.append(self.scene(sc, i, prev, surf))
            # Visibility window: an incoming scene shows from the start of the outgoing transition.
            pd = prev.transition_out.duration_s if prev and prev.transition_out.type != "cut" else 0.0
            show_from = min(sc.t_in, prev.t_out - pd) if prev else 0.0
            show_to = sc.t_out if i < n - 1 else self.comp.meta.duration_s + 1
            self.spec.scenes.append(
                {"id": sc.id, "from": round(show_from, 4), "to": round(show_to, 4), "z": n - i}
            )
            tr = sc.transition_out
            if i < n - 1 and tr.type != "cut" and tr.duration_s > 0:
                d = max(tr.duration_s, 0.35)
                t = sc.t_out - d
                nxt = scenes[i + 1]
                io = "cubic-bezier(.65,0,.35,1)"
                if tr.type == "wipe":
                    # Two brand-colored panels sweep across; the scenes swap underneath at the midpoint.
                    wid = f"wipe{i}"
                    overlays.append(
                        f'<div class="wipe" id="{wid}"><i class="p1"></i><i class="p2"></i></div>'
                    )
                    self.anim(f"#{wid} .p1", "panel_sweep", t, d, easing=io)
                    self.anim(f"#{wid} .p2", "panel_sweep", t + 0.06, d, easing=io)
                    self.anim(f"#{sc.id}", "out_hold", t, d, easing="linear")
                    wipes.append({"id": wid, "from": round(t, 4), "to": round(t + d + 0.08, 4)})
                elif tr.type == "zoom":
                    self.anim(f"#{sc.id}", "out_zoom", t, d, easing=io)
                    self.anim(f"#{nxt.id}", "in_zoom", t, d, easing=io)
                elif tr.type == "slide":
                    self.anim(f"#{sc.id}", "out_slide", t, d, easing=io)
                    self.anim(f"#{nxt.id}", "in_slide", t, d, easing=io)
                else:
                    self.anim(f"#{sc.id}", f"out_{tr.type}", t, d, easing=io)
        st = self.style
        rtl = self.comp.meta.language.split("-")[0].lower() in RTL_LANGS
        css = CSS.replace("__DISPLAY__", _stack(st.display_font)).replace("__BODY__", _stack(st.body_font))
        css = css.replace("__DW__", str(st.display_weight)).replace("__BW__", str(st.body_weight))
        css = css.replace("__TRACK__", f"{st.headline_tracking}em").replace("__RADIUS__", str(st.radius))
        css = css.replace("__UPPER__", "uppercase" if st.uppercase_headlines else "none")
        self.spec.overlays = wipes
        self.anim("#grain", "grain", 0, max(0.5, self.comp.meta.duration_s), easing="steps(240)")
        spec = json.dumps(self.spec.__dict__)
        return (
            '<!doctype html><html><head><meta charset="utf-8">'
            f"<style>{_font_faces()}\n{css}</style></head>"
            f'<body><div id="stage" dir="{"rtl" if rtl else "ltr"}" lang="{_e(self.comp.meta.language)}" '
            f'style="--wipe1:{self.colors.get("accent", "#FF5A1F")};--wipe2:{self.colors.get("primary", "#111111")}">'
            + "".join(layers)
            + "".join(overlays)
            + '<div id="grain"></div><div id="vignette"></div>'
            + f"</div><script>window.__SPEC={spec};\n{RUNTIME_JS}</script></body></html>"
        )


def build_page(
    comp: Composition, assets: dict[str, str], footage: dict[str, FootageFrames] | None = None
) -> str:
    """``assets`` maps asset ids to URLs relative to the page (or absolute file URLs); ``footage`` maps
    footage scene ids to their extracted frames (rendering only)."""
    return _Builder(comp, assets, footage).build()


CSS = r"""
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;overflow:hidden;background:#000}
#stage{position:relative;width:100vw;height:100vh;overflow:hidden;--u:calc(min(100vw,100vh)/100);
  font-family:__BODY__;font-weight:__BW__;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
.scene{position:absolute;inset:0;overflow:hidden;color:var(--ink);display:none}
.bg{position:absolute;inset:-4%;background:var(--bg)}
.bg-gradient{background:linear-gradient(160deg,var(--bg) 0%,var(--bg2) 120%)}
.bg-spotlight{background:radial-gradient(120% 80% at 50% 30%,var(--bg2) 0%,var(--bg) 58%)}
.bg-grid{background-color:var(--bg);background-image:linear-gradient(color-mix(in srgb,var(--ink) 7%,transparent) 1px,transparent 1px),
  linear-gradient(90deg,color-mix(in srgb,var(--ink) 7%,transparent) 1px,transparent 1px);
  background-size:calc(var(--u)*8) calc(var(--u)*8)}
.bg-grid::after{content:"";position:absolute;inset:0;background:radial-gradient(80% 70% at 50% 45%,transparent 30%,var(--bg) 95%)}
.bg-shapes .shape{position:absolute;border-radius:50%;filter:blur(calc(var(--u)*1.2))}
.bg-shapes .shape.a{width:calc(var(--u)*90);height:calc(var(--u)*90);right:-30%;top:-18%;background:var(--bg2);opacity:.55}
.bg-shapes .shape.b{width:calc(var(--u)*60);height:calc(var(--u)*60);left:-22%;bottom:-12%;background:var(--accent);opacity:.22}

.content{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;
  gap:calc(var(--u)*3.6);padding:calc(var(--u)*18) calc(var(--u)*8) calc(var(--u)*26)}
.landscape .content{padding:calc(var(--u)*9) calc(var(--u)*12)}
.square .content{padding:calc(var(--u)*10) calc(var(--u)*9)}
.align-center .content{align-items:center;text-align:center}
.align-left .content{align-items:flex-start;text-align:start}

.kicker{font-family:__BODY__;font-weight:600;font-size:calc(var(--u)*3.1);letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
.headline{font-family:__DISPLAY__;font-weight:__DW__;line-height:1.02;letter-spacing:__TRACK__;text-transform:__UPPER__;
  color:var(--ink);width:100%;overflow-wrap:normal;word-break:normal}
.headline .w{display:inline-block}
.subtext{color:var(--muted);line-height:1.35;width:100%;max-width:calc(var(--u)*88)}
.fit{flex-shrink:0}
.bar{width:calc(var(--u)*16);height:calc(var(--u)*1.1);background:var(--accent);border-radius:calc(var(--u)*1);transform-origin:left center}
.align-center .bar{transform-origin:center}

/* product reveal */
.tpl-footage .footage-wrap{position:absolute;inset:0;overflow:hidden;background:#000}.tpl-footage .footage{width:100%;height:100%;object-fit:cover;transform-origin:50% 45%}.tpl-footage .scrim{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,0) 38%,rgba(0,0,0,.72) 100%)}.tpl-footage .content{justify-content:flex-end}.tpl-footage .copy{position:relative;display:flex;flex-direction:column;gap:calc(var(--u)*2.4);width:100%;--ink:#fff;color:#fff}.tpl-footage .headline,.tpl-footage .subtext{color:#fff;text-shadow:0 calc(var(--u)*.3) calc(var(--u)*1.2) rgba(0,0,0,.45)}.tpl-footage .bg{display:none}
.tpl-product_reveal .content{justify-content:flex-end}
.tpl-product_reveal .media-wrap{position:absolute;left:0;right:0;top:calc(var(--u)*14);height:52%;display:grid;place-items:center}
.tpl-product_reveal .plate{position:absolute;width:calc(var(--u)*78);height:calc(var(--u)*78);border-radius:50%;background:var(--bg2);opacity:.9}
.tpl-product_reveal .media{position:relative;width:86%;height:100%;display:grid;place-items:center}
.tpl-product_reveal .product{max-width:100%;max-height:100%;object-fit:contain;filter:drop-shadow(0 calc(var(--u)*2.4) calc(var(--u)*3) rgba(0,0,0,.35))}
.tpl-product_reveal .copy{display:flex;flex-direction:column;gap:calc(var(--u)*2.6);width:100%}
.align-center .copy{align-items:center}
.landscape.tpl-product_reveal .media-wrap,.landscape .tpl-product_reveal .media-wrap{left:52%;right:4%;top:8%;height:84%}
.landscape .tpl-product_reveal .content{justify-content:center;padding-right:52%}
.landscape .tpl-product_reveal .plate{width:calc(var(--u)*70);height:calc(var(--u)*70)}
.square .tpl-product_reveal .media-wrap{height:46%;top:calc(var(--u)*6)}

/* device mockup */
.tpl-device_mockup .content{justify-content:flex-start;padding-top:calc(var(--u)*14)}
.tpl-device_mockup .copy{display:flex;flex-direction:column;gap:calc(var(--u)*2.2);width:100%}
.tpl-device_mockup .phone{position:absolute;left:calc(50% - var(--u)*29);bottom:calc(var(--u)*-14);width:calc(var(--u)*58);aspect-ratio:9/19.5;
  border-radius:calc(var(--u)*8);background:#0b0b0d;padding:calc(var(--u)*1.8);box-shadow:0 calc(var(--u)*4) calc(var(--u)*8) rgba(0,0,0,.45),0 0 0 calc(var(--u)*.5) color-mix(in srgb,var(--ink) 18%,transparent)}
.tpl-device_mockup .glass{width:100%;height:100%;border-radius:calc(var(--u)*6.4);overflow:hidden;background:#111}
.tpl-device_mockup .screen{width:100%;display:block}
.tpl-device_mockup .notch{position:absolute;top:calc(var(--u)*3.2);left:50%;transform:translateX(-50%);width:calc(var(--u)*16);height:calc(var(--u)*3.6);border-radius:calc(var(--u)*2);background:#0b0b0d;z-index:2}
.landscape .tpl-device_mockup .content{justify-content:center;padding-right:48%}
.landscape .tpl-device_mockup .phone{left:auto;right:12%;bottom:auto;top:8%;height:96%;width:auto}
.square .tpl-device_mockup .content{padding-right:46%;justify-content:center}
.square .tpl-device_mockup .phone{left:auto;right:6%;bottom:auto;top:10%;width:calc(var(--u)*40)}

/* feature list */
.items{list-style:none;display:flex;flex-direction:column;gap:calc(var(--u)*2.8);width:100%}
.items li{display:flex;align-items:center;gap:calc(var(--u)*3);text-align:start;width:100%}
.align-center .items{max-width:calc(var(--u)*84)}
.tick{flex:none;width:calc(var(--u)*8);height:calc(var(--u)*8);border-radius:calc(var(--u)*__RADIUS__);background:var(--accent);color:var(--accent-ink);display:grid;place-items:center}
.tick svg{width:62%;height:62%}
.items .it{flex:1;min-width:0;line-height:1.2;font-weight:600}

/* stats */
.stats{display:grid;gap:calc(var(--u)*5);width:100%}
.stats.n2,.stats.n3{grid-template-columns:1fr}
.landscape .stats.n2{grid-template-columns:1fr 1fr}.landscape .stats.n3{grid-template-columns:1fr 1fr 1fr}
.stat{display:flex;flex-direction:column;gap:calc(var(--u)*.8)}
.stat .value{font-family:__DISPLAY__;font-weight:__DW__;color:var(--accent);line-height:1;font-variant-numeric:tabular-nums}
.stat .label{color:var(--muted);font-size:calc(var(--u)*3.8);line-height:1.25}

/* testimonial */
.tpl-testimonial_card .card{position:relative;width:100%;padding:calc(var(--u)*9) calc(var(--u)*7) calc(var(--u)*7);border-radius:calc(var(--u)*__RADIUS__ * 1.6);
  background:color-mix(in srgb,var(--ink) 6%,var(--bg));display:flex;flex-direction:column;gap:calc(var(--u)*3)}
.qmark{position:absolute;top:calc(var(--u)*-6);font-family:'Playfair Display',serif;font-size:calc(var(--u)*24);line-height:1;color:var(--accent)}
.headline.quote{font-family:__BODY__;font-weight:600;text-transform:none;letter-spacing:-.01em;line-height:1.22}

/* price */
.price{font-family:__DISPLAY__;font-weight:__DW__;color:var(--accent);line-height:1;width:100%}
.chips{display:flex;flex-wrap:wrap;gap:calc(var(--u)*1.6)}
.align-center .chips{justify-content:center}
.chips span{padding:calc(var(--u)*1.2) calc(var(--u)*2.6);border-radius:999px;border:calc(var(--u)*.35) solid var(--accent);font-size:calc(var(--u)*3.2);font-weight:600}

/* logo sting */
.tpl-logo_sting .content{align-items:center;text-align:center}
.sting{position:relative;display:grid;place-items:center;width:100%;height:calc(var(--u)*50)}
.ring{position:absolute;width:calc(var(--u)*40);height:calc(var(--u)*40);border-radius:50%;border:calc(var(--u)*1) solid var(--accent);opacity:0}
.logo-hero{position:relative;display:grid;place-items:center;max-width:calc(var(--u)*64);max-height:calc(var(--u)*36)}
.logo-hero img{max-width:calc(var(--u)*64);max-height:calc(var(--u)*36);object-fit:contain}
.logo-text{font-family:__DISPLAY__;font-weight:__DW__;font-size:calc(var(--u)*12);line-height:1;text-transform:__UPPER__;color:var(--ink)}

/* CTA */
.cta{display:inline-flex;align-items:center;justify-content:center;padding:calc(var(--u)*3) calc(var(--u)*7);border-radius:999px;
  background:var(--accent);color:var(--accent-ink);font-weight:700;font-size:calc(var(--u)*5);box-shadow:0 calc(var(--u)*1.6) calc(var(--u)*4) color-mix(in srgb,var(--accent) 45%,transparent)}
.logo-small{display:flex;align-items:center;height:calc(var(--u)*10);background:var(--plate);
  padding:0 calc(var(--u)*var(--plate-pad)*2);border-radius:calc(var(--u)*2)}
.logo-small:not(.logo-text) img{max-height:calc(var(--u)*(10 - var(--plate-pad)*1.6))}
.logo-hero:not(.logo-text){background:var(--plate);padding:calc(var(--u)*var(--plate-pad)*2.2);border-radius:calc(var(--u)*3)}
.logo-small img{max-height:calc(var(--u)*10);max-width:calc(var(--u)*40);object-fit:contain}
.logo-small.logo-text{font-size:calc(var(--u)*6)}

/* ---- pro layers: camera, light, particles, accents, transitions, grain ---- */
.scene > .content{transform-origin:50% 55%;will-change:transform}
.fx{position:absolute;inset:0;pointer-events:none;overflow:hidden}
.glow{position:absolute;width:calc(var(--u)*110);height:calc(var(--u)*110);border-radius:50%;
  background:radial-gradient(circle,color-mix(in srgb,var(--accent) 55%,transparent) 0%,transparent 62%);opacity:.38;filter:blur(calc(var(--u)*2))}
.glow.gr{right:-38%;top:-22%}.glow.gl{left:-38%;bottom:-20%}
.streak{position:absolute;top:-30%;left:-60%;width:38%;height:160%;transform:rotate(18deg);
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.16),rgba(255,255,255,.04),transparent);opacity:0}
.dot{position:absolute;border-radius:50%;background:color-mix(in srgb,var(--accent) 70%,#fff)}
.corner{position:absolute;width:calc(var(--u)*14);height:calc(var(--u)*14);border-color:var(--accent);border-style:solid;opacity:.9}
.corner.tl{left:calc(var(--u)*5);top:calc(var(--u)*5);border-width:calc(var(--u)*.6) 0 0 calc(var(--u)*.6);transform-origin:0 0}
.corner.br{right:calc(var(--u)*5);bottom:calc(var(--u)*5);border-width:0 calc(var(--u)*.6) calc(var(--u)*.6) 0;transform-origin:100% 100%}
.headline .w{display:inline-block;overflow:hidden;vertical-align:top;padding-bottom:.08em;margin-bottom:-.08em}
.headline .wi{display:inline-block;will-change:transform}
.burst{position:absolute;width:calc(var(--u)*130);height:calc(var(--u)*130);border-radius:50%;opacity:.28;
  background:repeating-conic-gradient(from 0deg,color-mix(in srgb,var(--accent) 70%,transparent) 0deg 7deg,transparent 7deg 22deg);
  -webkit-mask:radial-gradient(circle,#000 18%,transparent 66%);mask:radial-gradient(circle,#000 18%,transparent 66%)}
.tpl-product_reveal .media{perspective:calc(var(--u)*120)}
.floaty{position:relative;display:grid;place-items:center;max-width:100%;max-height:100%;overflow:hidden;border-radius:calc(var(--u)*2)}
.floaty .product{position:relative}
.shine{position:absolute;inset:0;background:linear-gradient(105deg,transparent 35%,rgba(255,255,255,.55) 50%,transparent 65%);
  transform:translateX(-120%);mix-blend-mode:soft-light;pointer-events:none}
.cta{position:relative;overflow:hidden;gap:calc(var(--u)*2)}
.cta .arrow{display:inline-block;font-weight:800}
.wipe{position:absolute;inset:0;z-index:999;pointer-events:none;display:none}
.wipe i{position:absolute;inset:-2% -2%;transform:translateX(-102%)}
.wipe .p1{background:var(--wipe1,#FF5A1F)}.wipe .p2{background:var(--wipe2,#111)}
#grain{position:absolute;inset:-50%;z-index:1000;pointer-events:none;opacity:.07;mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")}
#vignette{position:absolute;inset:0;z-index:1001;pointer-events:none;background:radial-gradient(120% 95% at 50% 45%,transparent 55%,rgba(0,0,0,.28) 100%)}

.floaty.card{border:calc(var(--u)*1.1) solid #fff;border-radius:calc(var(--u)*3);transform:rotateY(-9deg) rotateX(4deg);
  box-shadow:0 calc(var(--u)*5) calc(var(--u)*9) rgba(0,0,0,.42),0 calc(var(--u)*1) calc(var(--u)*2) rgba(0,0,0,.25)}
.floaty.card .product{border-radius:calc(var(--u)*1.6);max-height:calc(var(--u)*72)}
.reflect{position:absolute;left:12%;right:12%;bottom:-6%;height:10%;border-radius:50%;
  background:radial-gradient(closest-side,rgba(0,0,0,.35),transparent);filter:blur(calc(var(--u)*1.2))}
"""
