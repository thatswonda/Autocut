/* Frame-stepped runtime. window.__ready() fits text and builds paused animations; window.__seek(t)
   puts every scene, animation and counter at exactly time t (seconds). Output depends only on t. */
(() => {
  "use strict";
  const S = window.__SPEC;
  const stage = document.getElementById("stage");
  const anims = [];
  let u = 1;

  const PRESETS = {
    fade: () => [{ opacity: 0 }, { opacity: 1 }],
    fade_up: () => [{ opacity: 0, transform: `translateY(${5 * u}px)` }, { opacity: 1, transform: "none" }],
    slide_left: () => [{ opacity: 0, transform: `translateX(${9 * u}px)` }, { opacity: 1, transform: "none" }],
    pop: () => [
      { opacity: 0, transform: `translateY(${2 * u}px) scale(.6)` },
      { opacity: 1, transform: "scale(1.06)", offset: 0.65 },
      { opacity: 1, transform: "scale(1)" },
    ],
    blur_in: () => [
      { opacity: 0, filter: `blur(${1.6 * u}px)`, transform: "scale(1.06)" },
      { opacity: 1, filter: "blur(0px)", transform: "scale(1)" },
    ],
    mask_reveal: () => [
      { clipPath: "inset(0 100% 0 0)", transform: `translateX(${-2 * u}px)` },
      { clipPath: "inset(0 0% 0 0)", transform: "none" },
    ],
    scale_in: () => [{ opacity: 0, transform: "scale(1.18)" }, { opacity: 1, transform: "scale(1)" }],
    rise: () => [{ opacity: 0, transform: `translateY(${16 * u}px)` }, { opacity: 1, transform: "none" }],
    bar_grow: () => [{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }],
    ken_burns: () => [{ transform: "scale(1)" }, { transform: "scale(1.07)" }],
    slow_zoom: () => [{ transform: "scale(1)" }, { transform: "scale(1.05)" }],
    pulse: () => [{ transform: "scale(1)" }, { transform: "scale(1.05)", offset: 0.5 }, { transform: "scale(1)" }],
    ring: () => [
      { opacity: 0.9, transform: "scale(.25)" },
      { opacity: 0, transform: "scale(2.3)" },
    ],
    drift_a: () => [{ transform: "translate(0,0)" }, { transform: `translate(${-8 * u}px, ${6 * u}px)` }],
    drift_b: () => [{ transform: "translate(0,0)" }, { transform: `translate(${7 * u}px, ${-5 * u}px)` }],
    grid_pan: () => [{ backgroundPosition: "0px 0px" }, { backgroundPosition: `${8 * u}px ${-16 * u}px` }],
    scroll: (el) => {
      const glass = el.parentElement;
      const travel = Math.max(0, el.getBoundingClientRect().height - glass.getBoundingClientRect().height);
      return [{ transform: "translateY(0)" }, { transform: `translateY(${-travel}px)` }];
    },
    out_fade: () => [{ opacity: 1 }, { opacity: 0 }],
    out_wipe: () => [{ clipPath: "inset(0 0 0 0%)" }, { clipPath: "inset(0 0 0 100%)" }],
    // Pro transitions: zoom-through with blur, push with motion blur, panel wipe (scene swaps at the middle).
    out_zoom: () => [
      { opacity: 1, transform: "scale(1)", filter: "blur(0px)" },
      { opacity: 1, transform: "scale(1.35)", filter: `blur(${0.8 * u}px)`, offset: 0.55 },
      { opacity: 0, transform: "scale(1.6)", filter: `blur(${1.6 * u}px)` },
    ],
    in_zoom: () => [
      { transform: "scale(.7)", filter: `blur(${1.6 * u}px)` },
      { transform: "scale(1)", filter: "blur(0px)" },
    ],
    out_slide: () => [
      { transform: "translateX(0)", filter: "blur(0px)" },
      { transform: "translateX(-50%)", filter: `blur(${1.2 * u}px)`, offset: 0.5 },
      { transform: "translateX(-100%)", filter: "blur(0px)" },
    ],
    in_slide: () => [
      { transform: "translateX(100%)", filter: "blur(0px)" },
      { transform: "translateX(50%)", filter: `blur(${1.2 * u}px)`, offset: 0.5 },
      { transform: "translateX(0)", filter: "blur(0px)" },
    ],
    out_hold: () => [{ opacity: 1 }, { opacity: 1, offset: 0.5 }, { opacity: 0, offset: 0.5001 }, { opacity: 0 }],
    panel_sweep: () => [{ transform: "translateX(-102%)" }, { transform: "translateX(0%)", offset: 0.5 }, { transform: "translateX(102%)" }],
    // Type.
    word_up: () => [
      { transform: "translateY(110%) rotate(6deg)", filter: `blur(${0.4 * u}px)` },
      { transform: "translateY(0) rotate(0)", filter: "blur(0px)" },
    ],
    word_blur: () => [
      { opacity: 0, transform: "scale(1.25)", filter: `blur(${1.4 * u}px)` },
      { opacity: 1, transform: "scale(1)", filter: "blur(0px)" },
    ],
    word_slide: () => [
      { transform: "translateX(-105%)", opacity: 0 },
      { transform: "translateX(0)", opacity: 1 },
    ],
    track_in: () => [{ opacity: 0, letterSpacing: "0.6em", filter: `blur(${0.3 * u}px)` }, { opacity: 1, letterSpacing: "0.14em", filter: "blur(0px)" }],
    soft_up: () => [{ opacity: 0, transform: `translateY(${4 * u}px)`, filter: `blur(${0.5 * u}px)` }, { opacity: 1, transform: "none", filter: "blur(0px)" }],
    // Camera and ambience.
    cam_push: () => [{ transform: "scale(1)" }, { transform: "scale(1.045)" }],
    cam_drift: () => [{ transform: "scale(1.08) translate(0,0)" }, { transform: `scale(1.0) translate(${-1.5 * u}px, ${1 * u}px)` }],
    float_up: (el) => {
      const h = el.parentElement.getBoundingClientRect().height;
      const seed = (parseFloat(el.style.left) * 13 + parseFloat(el.style.top) * 7) % 1;
      return [
        { transform: `translate(0, ${0.1 * h}px)`, opacity: 0 },
        { opacity: Number(el.style.opacity) || 0.5, offset: 0.2 },
        { opacity: Number(el.style.opacity) || 0.5, offset: 0.8 },
        { transform: `translate(${(seed - 0.5) * 8 * u}px, ${-0.35 * h}px)`, opacity: 0 },
      ];
    },
    glow_drift: () => [
      { transform: "translate(0,0) scale(1)", opacity: 0.25 },
      { transform: `translate(${-6 * u}px, ${5 * u}px) scale(1.15)`, opacity: 0.45, offset: 0.5 },
      { transform: `translate(${-10 * u}px, ${2 * u}px) scale(1.05)`, opacity: 0.3 },
    ],
    streak: () => [{ transform: "translateX(0) rotate(18deg)", opacity: 0 }, { opacity: 1, offset: 0.3 }, { transform: "translateX(420%) rotate(18deg)", opacity: 0 }],
    corner_draw: () => [{ transform: "scale(0)", opacity: 0 }, { transform: "scale(1)", opacity: 0.9 }],
    spin_in: () => [{ transform: "rotate(0deg) scale(.6)", opacity: 0 }, { transform: "rotate(25deg) scale(1)", opacity: 0.28, offset: 0.25 }, { transform: "rotate(70deg) scale(1.05)", opacity: 0.28 }],
    tilt_in: () => [
      { opacity: 0, transform: `translateY(${10 * u}px) rotateY(-28deg) rotateX(10deg) scale(.78)`, filter: `blur(${0.8 * u}px)` },
      { opacity: 1, transform: "translateY(0) rotateY(0) rotateX(0) scale(1)", filter: "blur(0px)" },
    ],
    // Uses the separate translate/rotate properties so a card's own 3D tilt (transform) is kept.
    bob: () => [{ translate: "0 0", rotate: "0deg" }, { translate: `0 ${-1.6 * u}px`, rotate: "-1deg", offset: 0.5 }, { translate: "0 0", rotate: "0deg" }],
    shine: () => [{ transform: "translateX(-120%)" }, { transform: "translateX(120%)" }],
    phone_in: () => [
      { opacity: 0, transform: `translateY(${30 * u}px) rotate(-8deg) scale(.9)` },
      { opacity: 1, transform: "translateY(0) rotate(0) scale(1)" },
    ],
    grain: () => [{ transform: "translate(0,0)" }, { transform: `translate(${-37 * u}px, ${23 * u}px)` }],
  };

  function tooBig(el) {
    const maxh = Number(el.dataset.maxh || 1e6) * u;
    return el.getBoundingClientRect().height > maxh + 0.5 || el.scrollWidth > el.clientWidth + 1;
  }

  function shrink(el, factor) {
    const min = Number(el.dataset.min) * u;
    const cur = parseFloat(el.style.fontSize);
    if (cur <= min) return false;
    el.style.fontSize = `${Math.max(min, cur * factor)}px`;
    return true;
  }

  /** Size every .fit element to its height budget, then make sure each scene's content fits the frame.
      Anything that still cannot fit is reported (shown to the user as a review warning). */
  function fitAll() {
    const warnings = [];
    for (const el of document.querySelectorAll(".fit")) {
      el.style.fontSize = `${Number(el.dataset.max) * u}px`;
      let guard = 0;
      while (tooBig(el) && guard++ < 120 && shrink(el, 0.95)) { /* keep shrinking */ }
      if (tooBig(el)) {
        el.style.overflowWrap = "anywhere";
        if (tooBig(el)) warnings.push(el.dataset.label || el.className);
      }
    }
    // Only in-flow content counts: decorative layers (phones, plates) may bleed past the frame on purpose.
    const spills = (content) => {
      const box = content.getBoundingClientRect();
      const cs = getComputedStyle(content);
      const top = box.top + parseFloat(cs.paddingTop) - 1, bottom = box.bottom - parseFloat(cs.paddingBottom) + 1;
      return [...content.children].some((ch) => {
        if (getComputedStyle(ch).position === "absolute") return false;
        const r = ch.getBoundingClientRect();
        return r.height > 0 && (r.top < top || r.bottom > bottom);
      });
    };
    for (const scene of document.querySelectorAll(".scene")) {
      const content = scene.querySelector(".content");
      const fits = [...scene.querySelectorAll(".fit")];
      let guard = 0;
      while (spills(content) && guard++ < 60) {
        if (!fits.map((el) => shrink(el, 0.94)).some(Boolean)) {
          warnings.push(`${scene.id} content`);
          break;
        }
      }
    }
    return warnings;
  }

  function build() {
    for (const a of S.anims) {
      const els = document.querySelectorAll(a.sel);
      els.forEach((el, i) => {
        const make = PRESETS[a.preset];
        if (!make) return;
        const anim = el.animate(make(el), {
          duration: a.dur * 1000,
          delay: (a.start + i * (a.stagger || 0)) * 1000,
          easing: a.easing || "linear",
          fill: "both",
          iterations: a.iter || 1,
        });
        anim.pause();
        anims.push(anim);
      });
    }
  }

  const ease = (p) => 1 - Math.pow(1 - p, 3);
  function counters(t) {
    for (const c of S.counters) {
      const el = document.getElementById(c.id);
      if (!el) continue;
      const p = Math.min(1, Math.max(0, (t - c.start) / c.dur));
      const v = c.to * ease(p);
      const num = v.toLocaleString("en-US", { minimumFractionDigits: c.dec, maximumFractionDigits: c.dec });
      el.textContent = `${c.prefix}${num}${c.suffix}`;
    }
  }

  // Footage scenes show pre-extracted frames; seeking swaps the image and waits until it is decoded.
  const footageImgs = {};
  function footage(t) {
    const waits = [];
    for (const f of S.footage || []) {
      if (t < f.from - 0.5 || t > f.to + 0.5) continue;
      const img = footageImgs[f.id] || (footageImgs[f.id] = document.querySelector(`[data-footage="${f.id}"]`));
      if (!img) continue;
      const idx = Math.max(0, Math.min(f.count - 1, Math.floor((t - f.from) * f.fps + 1e-6)));
      const src = `${f.prefix}${String(idx).padStart(5, "0")}.jpg`;
      if (img.getAttribute("src") !== src) {
        img.setAttribute("src", src);
        waits.push(img.decode().catch(() => {}));
      }
    }
    return waits;
  }

  window.__seek = (t) => {
    for (const sc of S.scenes) {
      const el = document.getElementById(sc.id);
      el.style.display = t >= sc.from - 1e-6 && t < sc.to ? "block" : "none";
    }
    for (const o of S.overlays || []) {
      const el = document.getElementById(o.id);
      if (el) el.style.display = t >= o.from - 1e-6 && t < o.to ? "block" : "none";
    }
    const ms = t * 1000;
    for (const a of anims) a.currentTime = ms;
    counters(t);
    const waits = footage(t);
    return waits.length ? Promise.all(waits).then(() => true) : true;
  };

  window.__ready = async () => {
    const w = window.innerWidth, h = window.innerHeight;
    stage.classList.add(w > h * 1.1 ? "landscape" : h > w * 1.1 ? "portrait" : "square");
    u = Math.min(w, h) / 100;
    await document.fonts.ready;
    await Promise.all([...document.images].map((img) => (img.complete ? img.decode().catch(() => {}) : new Promise((r) => { img.onload = img.onerror = r; }))));
    // Lay every scene out (visible) while fitting text, then hide them again.
    for (const sc of S.scenes) {
      const el = document.getElementById(sc.id);
      el.style.display = "block";
      el.style.zIndex = String(sc.z);
    }
    const warnings = fitAll();
    // Counters fit at their final value; start them from zero afterwards.
    build();
    window.__seek(0);
    return warnings;
  };
})();
