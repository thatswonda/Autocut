/* Autocut web app. Talks to the public HTTP API. In the hosted preview, window.AutocutDemoApi
   replaces the HTTP client with sample data. */
(() => {
  "use strict";

  // ------------------------------------------------------------------------------------ helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (v) =>
    String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmtDur = (s) => {
    s = Math.max(0, Math.round(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
  };
  const fmtTC = (s) => {
    s = Math.max(0, s || 0);
    const m = Math.floor(s / 60), r = s - m * 60;
    return `${String(m).padStart(2, "0")}:${r.toFixed(1).padStart(4, "0")}`;
  };
  const fmtBytes = (b) => (b > 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const VIDEO_EXT = [".mp4", ".mov", ".mkv", ".webm"];
  const IMAGE_EXT = [".png", ".jpg", ".jpeg", ".webp"];
  const extOf = (name) => (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  const URL_RE = /\b((?:https?:\/\/)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\/[^\s]*)?)/gi;
  const icon = {
    film: '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="3" width="12" height="10" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M5 3v10M11 3v10M2 6.5h3M11 6.5h3M2 9.5h3M11 9.5h3" stroke="currentColor" stroke-width="1.2"/></svg>',
    spark: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 1.8l1.6 4.1 4.1 1.6-4.1 1.6L8 13.2l-1.6-4.1L2.3 7.5l4.1-1.6L8 1.8z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    up: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 12.5v-9M4 7l4-4 4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    down: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 3.5v9M4 9l4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    send: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 13V3.5M3.8 7.6L8 3.4l4.2 4.2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    x: '<svg viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    plus: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    check: '<svg viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    dot: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="4" fill="currentColor"/></svg>',
    clock: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4"/><path d="M8 4.8V8l2 1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
    info: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.2" stroke="currentColor" stroke-width="1.4"/><path d="M8 7.2v4M8 4.8v.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    retry: '<svg viewBox="0 0 16 16" fill="none"><path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5v3h-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    image: '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="3" width="12" height="10" rx="2" stroke="currentColor" stroke-width="1.4"/><circle cx="6" cy="6.8" r="1.3" fill="currentColor"/><path d="M3 12l3.5-3.5 2.5 2.5 2-2L14 12" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    link: '<svg viewBox="0 0 16 16" fill="none"><path d="M6.8 9.2a2.8 2.8 0 0 0 4 0l2-2a2.8 2.8 0 0 0-4-4l-.7.7M9.2 6.8a2.8 2.8 0 0 0-4 0l-2 2a2.8 2.8 0 0 0 4 4l.7-.7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
    download: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 2.5v8M4.5 7.5L8 11l3.5-3.5M3 13.5h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    wand: '<svg viewBox="0 0 16 16" fill="none"><path d="M3 13l7.5-7.5M9.5 3.5l.4-1.4.4 1.4 1.4.4-1.4.4-.4 1.4-.4-1.4-1.4-.4 1.4-.4zM12.5 7.5l.3-1 .3 1 1 .3-1 .3-.3 1-.3-1-1-.3 1-.3z" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  let toastTimer = 0;
  function toast(msg, isError = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("err", isError);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), isError ? 5600 : 2600);
  }

  class ApiError extends Error {
    constructor(status, code, message, details) {
      super(message);
      Object.assign(this, { status, code, details: details || {} });
    }
  }

  // ------------------------------------------------------------------------------- HTTP client
  class HttpApi {
    constructor(base = "") { this.base = base; this.chunk = 8 * 1024 * 1024; }
    async req(method, path, body, raw = false) {
      const opts = { method, headers: {} };
      if (body instanceof FormData) opts.body = body;
      else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
      const res = await fetch(this.base + path, opts);
      if (res.status === 204) return null;
      let data = null;
      try { data = await res.json(); } catch { /* empty body */ }
      if (!res.ok) {
        const e = data?.error || {};
        throw new ApiError(res.status, e.code || "error", e.message || `Request failed (${res.status})`, e.details);
      }
      return raw ? res : data;
    }
    me() { return this.req("GET", "/v1/me"); }
    catalog() { return this.req("GET", "/v1/catalog"); }
    templates() { return this.req("GET", "/v1/templates"); }
    saveTemplate(body) { return this.req("POST", "/v1/templates", body); }
    deleteTemplate(id) { return this.req("DELETE", `/v1/templates/${id}`); }
    listProjects() { return this.req("GET", "/v1/projects?limit=100").then((r) => r.items); }
    getProject(id) { return this.req("GET", `/v1/projects/${id}`); }
    createProject(body) { return this.req("POST", "/v1/projects", body); }
    addClip(pid, uploadId) { return this.req("POST", `/v1/projects/${pid}/clips`, { upload_id: uploadId }); }
    removeClip(pid, cid) { return this.req("DELETE", `/v1/projects/${pid}/clips/${cid}`); }
    reorder(pid, ids) { return this.req("PUT", `/v1/projects/${pid}/clips/order`, { clip_ids: ids }); }
    retryClip(pid, cid) { return this.req("POST", `/v1/projects/${pid}/clips/${cid}/retry`); }
    deleteProject(pid) { return this.req("DELETE", `/v1/projects/${pid}`); }
    signals(clip) { return this.req("GET", clip.signals_url); }
    proxyUrl(clip) { return clip.proxy_url; }
    signalsDownloadUrl(clip) { return clip.signals_url; }
    createRender(pid, body) { return this.req("POST", `/v1/projects/${pid}/renders`, body); }
    putComposition(pid, comp) { return this.req("PUT", `/v1/projects/${pid}/composition`, comp); }
    putBrandKit(pid, body) { return this.req("PUT", `/v1/projects/${pid}/brand-kit`, body); }
    regenerate(pid, sid, note) { return this.req("POST", `/v1/projects/${pid}/scenes/${sid}/regenerate`, { note }); }
    retryPlan(pid) { return this.req("POST", `/v1/projects/${pid}/plan/retry`); }
    uploadImage(file, kind) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("kind", kind);
      return this.req("POST", "/v1/assets", fd);
    }

    /** Resumable chunked upload; survives dropped connections by asking the server for its offset. */
    async upload(file, onProgress, signal) {
      const up = await this.req("POST", "/v1/uploads", { filename: file.name, size_bytes: file.size, content_type: file.type || null });
      let offset = up.offset || 0;
      let failures = 0;
      while (offset < file.size) {
        if (signal?.aborted) throw new ApiError(0, "aborted", "Upload cancelled");
        const end = Math.min(file.size, offset + this.chunk);
        try {
          const res = await fetch(`${this.base}/v1/uploads/${up.id}`, {
            method: "PATCH",
            headers: { "Upload-Offset": String(offset), "Content-Type": "application/offset+octet-stream" },
            body: file.slice(offset, end),
            signal,
          });
          if (res.status === 409) {
            const head = await fetch(`${this.base}/v1/uploads/${up.id}`, { method: "HEAD" });
            offset = Number(head.headers.get("Upload-Offset") || offset);
            continue;
          }
          if (!res.ok) {
            const e = (await res.json().catch(() => ({}))).error || {};
            throw new ApiError(res.status, e.code || "upload_failed", e.message || "Upload failed");
          }
          offset = Number(res.headers.get("Upload-Offset") || end);
          failures = 0;
          onProgress(offset / file.size);
        } catch (err) {
          if (err instanceof ApiError || err.name === "AbortError") throw err;
          if (++failures > 6) throw new ApiError(0, "network", "Connection lost. Check your network and try again.");
          await new Promise((r) => setTimeout(r, Math.min(15000, 1000 * 2 ** failures)));
          const head = await fetch(`${this.base}/v1/uploads/${up.id}`, { method: "HEAD" }).catch(() => null);
          if (head?.ok) offset = Number(head.headers.get("Upload-Offset") || offset);
        }
      }
      onProgress(1, "checking");
      return this.req("POST", `/v1/uploads/${up.id}/complete`, {});
    }

    /** Live project updates over server-sent events, with a polling fallback. */
    watch(pid, onSnapshot) {
      let closed = false, es = null, timer = 0;
      const busy = (p) => ["analyzing", "planning", "rendering"].includes(p.status);
      const poll = async () => {
        if (closed) return;
        try {
          const p = await this.getProject(pid);
          onSnapshot(p);
          if (busy(p)) timer = setTimeout(poll, 1500);
        } catch { timer = setTimeout(poll, 4000); }
      };
      if (window.EventSource) {
        es = new EventSource(`${this.base}/v1/projects/${pid}/events`);
        es.addEventListener("project", (ev) => onSnapshot(JSON.parse(ev.data)));
        es.addEventListener("settled", () => es.close());
        es.onerror = () => { es.close(); if (!closed) poll(); };
      } else poll();
      return () => { closed = true; es?.close(); clearTimeout(timer); };
    }
  }

  const api = window.AutocutDemoApi ? new window.AutocutDemoApi() : new HttpApi();
  const DEMO = !!window.AutocutDemoApi;

  // ------------------------------------------------------------------------------------- state
  const EXAMPLES = {
    edit: [
      "Cut this into a punchy 45-second Reel",
      "Tighten my talking-head video: remove pauses and dead air",
      "Make a calm 2-minute YouTube version, keep every step",
    ],
    motion: [
      "30-second promo for my bakery. Warm and friendly, end with 'Order today'",
      "Launch video for our budgeting app, highlight the 3 main features",
      "Product ad for these sneakers, bold and energetic",
    ],
  };
  const PLACEHOLDER = {
    edit: "Describe the edit you want: platform, length, tone, anything to keep or cut…",
    motion: "Tell Autocut about the business and the video: what it sells, who it's for, the offer, and the call to action…",
  };
  const ASPECT_LABEL = { "9:16": "9:16 Vertical", "1:1": "1:1 Square", "4:5": "4:5 Portrait", "16:9": "16:9 Wide" };
  const TEMPLATE_LABEL = {
    kinetic_text: "Big statement", product_reveal: "Product reveal", device_mockup: "App on a phone",
    feature_list: "Feature list", stats_counter: "Numbers", testimonial_card: "Testimonial",
    price_offer: "Price / offer", logo_sting: "Logo moment", cta_end_card: "Call to action", footage: "Footage",
  };

  const state = {
    me: null,
    catalog: { color_presets: [], motion_styles: [], ai_enabled: false },
    templates: [],
    projects: [],
    route: { view: "new" },
    project: null,
    selectedClip: null,
    signals: new Map(),
    posters: new Map(),
    stopWatch: null,
    confirm: null,
    sb: null, // storyboard working copy {pid, version, comp, dirty}
    ui: { renderId: null, markIn: null, markOut: null, showOptions: false, renderOpts: {} },
    draft: {
      mode: "edit",
      prompt: "",
      clips: [],
      images: [],
      links: [],
      menu: false,
      linkOpen: false,
      authorized: false,
      introId: null,
      outroId: null,
      poster: null,
      edit: { aspect: "9:16", color_preset: "natural", target: "" },
      motion: { aspect: "9:16", duration_s: 20, style: "bold_pop" },
    },
  };
  const limitS = () => state.me?.limits?.max_total_raw_s ?? 1800;
  const maxClips = () => state.me?.limits?.max_clips ?? 20;
  const presetByKey = (k) => state.catalog.color_presets.find((p) => p.key === k);

  // ------------------------------------------------------------------------------------ routing
  function go(route) {
    state.route = route;
    state.confirm = null;
    state.ui = { renderId: null, markIn: null, markOut: null, showOptions: false, renderOpts: {} };
    try { location.hash = route.view === "project" ? `p-${route.id}` : "new"; } catch { /* sandboxed */ }
    if (state.stopWatch) { state.stopWatch(); state.stopWatch = null; }
    if (route.view === "project") openProject(route.id);
    else { state.project = null; render(); }
  }
  function routeFromHash() {
    const h = (location.hash || "").slice(1);
    if (h.startsWith("p-")) return { view: "project", id: h.slice(2) };
    return { view: "new" };
  }

  async function refreshProjects() {
    try { state.projects = await api.listProjects(); } catch (e) { toast(e.message, true); }
    renderSidebar();
  }
  async function refreshTemplates() {
    try { state.templates = await api.templates(); } catch { state.templates = []; }
  }

  const BUSY = ["analyzing", "planning", "rendering"];
  function startWatch(id) {
    if (state.stopWatch) state.stopWatch();
    state.stopWatch = api.watch(id, applySnapshot);
  }

  async function openProject(id) {
    state.project = null;
    state.selectedClip = null;
    state.sb = null;
    render();
    try {
      const p = await api.getProject(id);
      applySnapshot(p);
      if (BUSY.includes(p.status)) startWatch(id);
    } catch (e) {
      toast(e.code === "project_not_found" ? "That project no longer exists." : e.message, true);
      go({ view: "new" });
    }
  }

  function applySnapshot(p) {
    if (state.route.view !== "project" || state.route.id !== p.id) return;
    const prev = state.project;
    state.project = p;
    if (p.mode === "edit") {
      if (!state.selectedClip || !p.clips.some((c) => c.id === state.selectedClip)) {
        state.selectedClip = (p.clips.find((c) => c.status === "ready") || p.clips[0])?.id || null;
      }
    }
    if (p.motion?.composition && (!state.sb || state.sb.pid !== p.id || (!state.sb.dirty && state.sb.version !== p.motion.version))) {
      state.sb = { pid: p.id, version: p.motion.version, comp: clone(p.motion.composition), dirty: false };
    }
    const latest = p.renders?.[0];
    if (latest && prev?.renders?.[0]?.id !== latest.id) state.ui.renderId = latest.id;
    const item = state.projects.find((x) => x.id === p.id);
    if (item) { item.status = p.status; item.clip_count = p.clips.length; item.total_raw_s = p.meter.total_raw_s; }
    renderSidebar();
    renderProject();
    if (prev && prev.status !== p.status) {
      if (p.status === "done" && prev.status === "rendering") toast("Your video is ready");
      else if (p.status === "review" && prev.status === "planning") toast(prev.motion?.pending_scene ? "Scene rewritten" : "Storyboard ready for review");
      else if (prev.status === "analyzing" && p.status === "ready") toast("Analysis finished. Create your edit when you're ready.");
      else if (p.status === "failed") toast("Something needs your attention", true);
    }
  }

  // ------------------------------------------------------------------------------------ sidebar
  const STATUS_DOT = { ready: "ready", done: "ready", review: "ready", analyzing: "analyzing", planning: "analyzing", rendering: "analyzing", failed: "failed", partial: "partial" };
  function renderSidebar() {
    const list = $("#project-list");
    if (!state.projects.length) {
      list.innerHTML = `<div class="muted" style="padding:8px 10px;font-size:13px">No projects yet.</div>`;
    } else {
      list.innerHTML = state.projects.map((p) => `
        <button class="project-item" type="button" data-open="${esc(p.id)}" aria-current="${state.route.view === "project" && state.route.id === p.id}">
          <span class="name">${esc(p.title || "Untitled project")}</span>
          <span class="status-dot ${STATUS_DOT[p.status] || ""}" title="${esc(p.status)}"></span>
          <span class="meta">${p.mode === "motion" ? "Motion graphics" : `${p.clip_count} clip${p.clip_count === 1 ? "" : "s"} · ${fmtDur(p.total_raw_s)}`}</span>
        </button>`).join("");
    }
    const sel = $("#mobile-project-select");
    sel.innerHTML = `<option value="new">+ New project</option>` + state.projects
      .map((p) => `<option value="${esc(p.id)}" ${state.route.id === p.id ? "selected" : ""}>${esc(p.title || "Untitled project")}</option>`).join("");
    if (state.route.view === "new") sel.value = "new";
  }

  function render() {
    renderSidebar();
    if (state.route.view === "project") renderProject();
    else renderComposer();
  }

  // =========================================================================== composer
  const draftTotal = () => state.draft.clips.reduce((s, c) => s + (c.duration || 0), 0);

  function chipHtml(kind, item) {
    if (kind === "clip") {
      const c = item;
      let sub;
      if (c.status === "uploading") sub = `Uploading ${pct(c.progress)} · ${fmtBytes(c.size)}`;
      else if (c.status === "checking") sub = "Checking the file…";
      else if (c.status === "error") sub = esc(c.error);
      else sub = `${fmtDur(c.duration)}${c.upload?.media ? ` · ${c.upload.media.display_width}×${c.upload.media.display_height}` : ""}`;
      return `<div class="chip ${c.status === "error" ? "err" : ""}">
        <span class="thumb" style="${c.thumb ? `background-image:url(${c.thumb})` : ""}">${c.thumb ? "" : icon.film}</span>
        <span class="meta"><b title="${esc(c.name)}">${esc(c.name)}</b><span>${sub}</span>
        ${c.status === "uploading" || c.status === "checking" ? `<span class="bar"><i style="width:${(c.progress * 100).toFixed(1)}%"></i></span>` : ""}</span>
        <button type="button" class="x" data-rm-clip="${c.key}" aria-label="Remove ${esc(c.name)}">${icon.x}</button></div>`;
    }
    if (kind === "image") {
      const im = item;
      const kinds = [["logo", "Logo"], ["photo", "Photo"], ["screenshot", "App screen"]];
      return `<div class="chip ${im.status === "error" ? "err" : ""}">
        <span class="thumb" style="background-image:url(${im.thumb})"></span>
        <span class="meta"><b title="${esc(im.name)}">${esc(im.name)}</b>
          ${im.status === "error" ? `<span>${esc(im.error)}</span>` : im.status === "uploading" ? "<span>Uploading…</span>" :
            `<select data-img-kind="${im.key}" aria-label="Image type">${kinds.map(([k, l]) => `<option value="${k}" ${im.kind === k ? "selected" : ""}>${l}</option>`).join("")}</select>`}
        </span>
        <button type="button" class="x" data-rm-img="${im.key}" aria-label="Remove ${esc(im.name)}">${icon.x}</button></div>`;
    }
    return `<div class="chip"><span class="thumb">${icon.link}</span>
      <span class="meta"><b title="${esc(item)}">${esc(item.replace(/^https?:\/\//, "").replace(/\/$/, ""))}</b><span>Website</span></span>
      <button type="button" class="x" data-rm-link="${esc(item)}" aria-label="Remove link">${icon.x}</button></div>`;
  }

  function composerState() {
    const d = state.draft;
    const editing = d.mode === "edit";
    const uploading = d.clips.some((c) => c.status === "uploading" || c.status === "checking") || d.images.some((i) => i.status === "uploading");
    if (editing) {
      const ready = d.clips.filter((c) => c.status === "ready");
      const errors = d.clips.filter((c) => c.status === "error");
      const total = draftTotal();
      if (!d.clips.length) return { can: false, note: "Add at least one video with the + button or drag it here." };
      if (uploading) return { can: false, note: "Uploading… you can keep typing." };
      if (errors.length) return { can: false, note: "Remove the videos marked in red to continue." };
      if (total > limitS() + 0.01) return { can: false, note: `Over the ${fmtDur(limitS())} limit. Remove a clip.` };
      return { can: ready.length > 0, note: `${ready.length} video${ready.length === 1 ? "" : "s"} ready · ${fmtDur(total)} of ${fmtDur(limitS())}` };
    }
    if (!state.catalog.ai_enabled) return { can: false, note: "Motion graphics needs an Anthropic API key on the server.", warn: true };
    if (uploading) return { can: false, note: "Uploading images…" };
    if (!d.links.length && !d.images.length && d.prompt.trim().length < 12) return { can: false, note: "Add a website link, a logo or photos, or describe the business." };
    if (!d.authorized) return { can: false, note: "Tick the box to confirm you can use this brand." };
    return { can: true, note: `${d.links.length} link${d.links.length === 1 ? "" : "s"} · ${d.images.length} image${d.images.length === 1 ? "" : "s"}` };
  }

  function colorTiles(selected, posterUrl, attr) {
    return state.catalog.color_presets.map((p) => `
      <button type="button" class="tile" ${attr}="${p.key}" aria-pressed="${p.key === selected}" title="${esc(p.description)}">
        <span class="pic">${posterUrl ? `<img src="${posterUrl}" alt="" style="filter:${esc(p.css)}">` : `<span class="scenic" style="filter:${esc(p.css)};position:relative"></span>`}</span>
        <span class="name">${esc(p.name)}</span></button>`).join("");
  }

  function styleTiles(selected, attr) {
    return state.catalog.motion_styles.map((s) => {
      const [bg, ink, acc] = s.colors;
      return `<button type="button" class="tile" ${attr}="${s.key}" aria-pressed="${s.key === selected}" title="${esc(s.description)}">
        <span class="pic"><span class="stylepic" style="background:${bg};color:${ink}">
          <span class="aa" style="font-family:'${esc(s.display_font)}';${s.uppercase ? "text-transform:uppercase;" : ""}font-weight:700">Aa Bb</span>
          <span class="ln" style="background:${acc}"></span><span class="ln2" style="background:${ink}"></span></span></span>
        <span class="name">${esc(s.name)}</span><span class="sub">${esc(s.description)}</span></button>`;
    }).join("");
  }

  function myTemplateTiles(introId, outroId, prefix) {
    if (!state.templates.length) return "";
    return `<div class="trow">
      <div class="trow-head"><h3>My templates</h3><span>Parts you saved from earlier videos. Add one as an intro or outro.</span></div>
      <div class="tiles">${state.templates.map((t) => `
        <div class="tile" style="cursor:default">
          <span class="pic">${t.poster_url ? `<img src="${esc(t.poster_url)}" alt="">` : ""}<span class="badge">${esc(t.kind)}</span></span>
          <span class="name">${esc(t.name)}</span><span class="sub">${fmtDur(t.duration_s)}</span>
          <span class="pick"><button type="button" data-${prefix}-intro="${t.id}" aria-pressed="${introId === t.id}">Intro</button>
          <button type="button" data-${prefix}-outro="${t.id}" aria-pressed="${outroId === t.id}">Outro</button></span>
        </div>`).join("")}</div></div>`;
  }

  function renderComposer() {
    const d = state.draft;
    const editing = d.mode === "edit";
    const cs = composerState();
    const chips = editing
      ? d.clips.map((c) => chipHtml("clip", c)).join("")
      : d.images.map((i) => chipHtml("image", i)).join("") + d.links.map((l) => chipHtml("link", l)).join("");
    const setAside = editing ? (d.images.length + d.links.length) : d.clips.length;
    $("#view").innerHTML = `
      <div class="wrap">
        <div class="greet">
          <span class="eyebrow">New project</span>
          <h1>${editing ? "What are we editing today?" : "What should we make for your business?"}</h1>
          <p>${editing
            ? "Drop your raw footage, say what you want, pick a look. Autocut cuts the dead air, reframes and grades it, and you can download the result."
            : "Add your website, logo or product photos and describe the video. Claude writes a storyboard from your real facts, you approve it, Autocut animates it."}</p>
        </div>
        <section class="box" id="box" aria-label="Prompt">
          ${d.linkOpen && !editing ? `<div class="linkrow"><input id="link-input" type="url" placeholder="https://yourbusiness.com" autocomplete="off">
            <button type="button" class="btn small" data-action="add-link">Add link</button><button type="button" class="btn small ghost" data-action="close-link">Cancel</button></div>` : ""}
          <div class="attach">${chips}</div>
          <textarea id="prompt" rows="2" placeholder="${esc(PLACEHOLDER[d.mode])}">${esc(d.prompt)}</textarea>
          <div class="toolbar">
            <span class="menu">
              <button type="button" class="tbtn icon" data-action="menu" aria-label="Add files or links" aria-expanded="${d.menu}">${icon.plus}</button>
              ${d.menu ? `<div class="menu-pop" role="menu">
                ${editing ? `<button type="button" data-action="pick-video">${icon.film}<span>Upload videos<small>MP4, MOV, MKV, WebM · resumable</small></span></button>`
                  : `<button type="button" data-action="pick-image">${icon.image}<span>Add logo or photos<small>PNG, JPG, WebP · up to 12</small></span></button>
                     <button type="button" data-action="open-link">${icon.link}<span>Add website link<small>Autocut reads it for facts, colors, logo</small></span></button>`}
              </div>` : ""}
            </span>
            <span class="seg" role="group" aria-label="Mode">
              <button type="button" data-mode="edit" aria-pressed="${editing}">${icon.film} Edit video</button>
              <button type="button" data-mode="motion" aria-pressed="${!editing}">${icon.spark} Motion graphics</button>
            </span>
            <select class="tselect" id="opt-aspect" aria-label="Format">${Object.entries(ASPECT_LABEL).map(([k, l]) =>
              `<option value="${k}" ${(editing ? d.edit.aspect : d.motion.aspect) === k ? "selected" : ""}>${l}</option>`).join("")}</select>
            ${editing ? `<select class="tselect" id="opt-target" aria-label="Length"><option value="">Any length</option>${[15, 30, 45, 60, 90, 120, 180].map((s) =>
                `<option value="${s}" ${String(d.edit.target) === String(s) ? "selected" : ""}>~${fmtDur(s)}</option>`).join("")}</select>`
              : `<select class="tselect" id="opt-duration" aria-label="Length">${[10, 15, 20, 30, 45, 60].map((s) =>
                `<option value="${s}" ${d.motion.duration_s === s ? "selected" : ""}>${s} seconds</option>`).join("")}</select>`}
            <button type="button" class="send" data-action="send" aria-label="Send" ${cs.can ? "" : "disabled"}>${icon.send}</button>
          </div>
        </section>
        <div class="belowbox">
          ${!editing ? `<label class="authline" for="auth"><input type="checkbox" id="auth" ${d.authorized ? "checked" : ""}>
            <span>I own this brand or am authorized to use its name, logo, images and website content.</span></label>` : ""}
          <div class="hintline"><span class="${cs.warn ? "warn" : ""}">${esc(cs.note)}</span>
            ${setAside ? `<span>${setAside} item${setAside === 1 ? "" : "s"} kept for ${editing ? "Motion graphics" : "Edit video"}</span>` : ""}</div>
          ${editing ? `<div class="trow">
              <div class="trow-head"><h3>Color grade</h3><span>${d.poster ? "Previewed on your first clip" : "Add a video to preview the look on it"}</span></div>
              <div class="tiles">${colorTiles(d.edit.color_preset, d.poster, "data-grade")}</div></div>`
            : `<div class="trow">
              <div class="trow-head"><h3>Motion style</h3><span>Fonts, motion and layout. Your brand colors are applied on top.</span></div>
              <div class="tiles">${styleTiles(d.motion.style, "data-style")}</div></div>`}
          ${myTemplateTiles(d.introId, d.outroId, "d")}
          <div class="trow"><div class="trow-head"><h3>Try</h3></div>
            <div class="examples" style="padding:0">${EXAMPLES[d.mode].map((t) => `<button type="button" class="example" data-example="${esc(t)}">${esc(t)}</button>`).join("")}</div></div>
          ${DEMO ? `<div class="banner">${icon.info}<span>Hosted preview with sample data. Uploads are simulated here.</span></div>` : ""}
        </div>
      </div>`;
    autoGrow($("#prompt"));
    bindDrop();
  }

  function autoGrow(ta) {
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(260, ta.scrollHeight)}px`;
  }

  function rerenderComposer() {
    if (state.route.view !== "new") return;
    const ta = $("#prompt");
    const focused = document.activeElement === ta;
    const pos = ta ? [ta.selectionStart, ta.selectionEnd] : null;
    renderComposer();
    if (focused) { const n = $("#prompt"); n.focus(); n.setSelectionRange(pos[0], pos[1]); }
  }

  function bindDrop() {
    const box = $("#box");
    if (!box) return;
    box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("over"); });
    box.addEventListener("dragleave", () => box.classList.remove("over"));
    box.addEventListener("drop", (e) => {
      e.preventDefault();
      box.classList.remove("over");
      addFiles([...(e.dataTransfer?.files || [])]);
    });
  }

  function localMeta(file) {
    return new Promise((resolve) => {
      const v = document.createElement("video");
      const url = URL.createObjectURL(file);
      const done = (d) => { resolve(d); };
      const t = setTimeout(() => done({ duration: null, url }), 6000);
      v.preload = "auto";
      v.muted = true;
      v.onloadedmetadata = () => { v.currentTime = Math.min(1.5, (v.duration || 2) / 3); };
      v.onseeked = () => {
        clearTimeout(t);
        let thumb = null;
        try {
          const c = document.createElement("canvas");
          c.width = 240; c.height = Math.round(240 * (v.videoHeight / v.videoWidth || 0.5625));
          c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
          thumb = c.toDataURL("image/jpeg", 0.7);
        } catch { /* unsupported codec preview */ }
        done({ duration: Number.isFinite(v.duration) ? v.duration : null, thumb, url });
      };
      v.onerror = () => { clearTimeout(t); done({ duration: null, url }); };
      v.src = url;
    });
  }

  let keySeq = 0;
  async function addFiles(files, intoProject = null) {
    const videos = files.filter((f) => VIDEO_EXT.includes(extOf(f.name)));
    const images = files.filter((f) => IMAGE_EXT.includes(extOf(f.name)));
    const other = files.filter((f) => !videos.includes(f) && !images.includes(f));
    if (other.length) toast(`${other[0].name} isn't a supported video or image.`, true);
    if (intoProject) { for (const f of videos) uploadIntoProject(f, intoProject); return; }
    if (videos.length && state.draft.mode !== "edit") { state.draft.mode = "edit"; toast("Switched to Edit video for your clips"); }
    else if (images.length && !videos.length && state.draft.mode !== "motion") { state.draft.mode = "motion"; toast("Switched to Motion graphics for your images"); }
    for (const file of videos) {
      if (state.draft.clips.length >= maxClips()) { toast(`A project holds at most ${maxClips()} clips.`, true); break; }
      const meta = await localMeta(file);
      if (meta.duration && draftTotal() + meta.duration > limitS() + 0.01) {
        toast(`${file.name} (${fmtDur(meta.duration)}) would go past the ${fmtDur(limitS())} limit.`, true);
        continue;
      }
      const clip = { key: `k${++keySeq}`, file, name: file.name, size: file.size, duration: meta.duration || 0, thumb: meta.thumb, status: "uploading", progress: 0 };
      if (!state.draft.poster && meta.thumb) state.draft.poster = meta.thumb;
      state.draft.clips.push(clip);
      rerenderComposer();
      startUpload(clip);
    }
    for (const file of images) {
      if (state.draft.images.length >= 12) { toast("Up to 12 images per project.", true); break; }
      const guess = /logo/i.test(file.name) || (!state.draft.images.some((i) => i.kind === "logo") && /\.png$/i.test(file.name) && state.draft.images.length === 0) ? "logo" : /screen|app/i.test(file.name) ? "screenshot" : "photo";
      const im = { key: `i${++keySeq}`, file, name: file.name, kind: guess, thumb: URL.createObjectURL(file), status: "uploading" };
      state.draft.images.push(im);
      rerenderComposer();
      uploadImage(im);
    }
  }

  async function uploadImage(im) {
    try {
      const a = await api.uploadImage(im.file, im.kind);
      im.asset = a;
      im.status = "ready";
    } catch (e) { im.status = "error"; im.error = e.message; }
    rerenderComposer();
  }

  async function startUpload(clip) {
    clip.ctrl = new AbortController();
    let last = 0;
    try {
      const up = await api.upload(clip.file, (p, phase) => {
        clip.progress = p;
        if (phase === "checking") clip.status = "checking";
        const now = performance.now();
        if (now - last > 150 || p >= 1) { last = now; rerenderComposer(); }
      }, clip.ctrl.signal);
      clip.upload = up;
      clip.duration = up.media.duration_s;
      clip.status = draftTotal() > limitS() + 0.01 ? "error" : "ready";
      if (clip.status === "error") clip.error = `Over the ${fmtDur(limitS())} total limit.`;
    } catch (e) {
      if (e.code === "aborted" || e.name === "AbortError") return;
      clip.status = "error";
      clip.error = e.message;
    }
    rerenderComposer();
  }

  async function uploadIntoProject(file, pid) {
    toast(`Uploading ${file.name}…`);
    try {
      const up = await api.upload(file, () => {});
      await api.addClip(pid, up.id);
      toast(`${file.name} added`);
      if (state.route.view === "project" && state.route.id === pid) {
        applySnapshot(await api.getProject(pid));
        startWatch(pid);
      }
      refreshProjects();
    } catch (e) { toast(e.message, true); }
  }

  function normaliseLink(raw) {
    let u = (raw || "").trim();
    if (!u) return null;
    if (!/^https?:\/\//i.test(u)) u = `https://${u}`;
    try { const x = new URL(u); return x.hostname.includes(".") ? x.href : null; } catch { return null; }
  }

  /** "a 30-second Reel", "2 minutes", "45s" -> seconds (the first length the prompt mentions). */
  function lengthFromPrompt(text) {
    const m = (text || "").toLowerCase().match(/(\d+(?:\.\d+)?)\s*-?\s*(seconds?|secs?|s\b|minutes?|mins?|m\b)/);
    if (!m) return null;
    const n = parseFloat(m[1]);
    return /^m/.test(m[2]) ? n * 60 : n;
  }

  async function send() {
    const d = state.draft;
    const asked = lengthFromPrompt(d.prompt);
    const cs = composerState();
    if (!cs.can) { toast(cs.note, true); return; }
    const btn = $('[data-action="send"]');
    if (btn) btn.disabled = true;
    const words = d.prompt.trim().replace(URL_RE, "").trim().split(/\s+/).filter(Boolean).slice(0, 7).join(" ");
    try {
      let body;
      if (d.mode === "edit") {
        const ready = d.clips.filter((c) => c.status === "ready");
        body = {
          mode: "edit",
          title: (words || ready[0]?.name.replace(/\.[^.]+$/, "") || "Untitled edit").slice(0, 120),
          prompt: d.prompt.trim(),
          options: { aspect: d.edit.aspect, color_preset: d.edit.color_preset,
                     target_duration_s: d.edit.target ? Number(d.edit.target) : asked && asked >= 5 ? asked : null,
                     intro_template_id: d.introId, outro_template_id: d.outroId },
          upload_ids: ready.map((c) => c.upload.id),
        };
      } else {
        const typed = (d.prompt.match(URL_RE) || []).map(normaliseLink).filter(Boolean);
        const links = [...new Set([...d.links, ...typed])].slice(0, 5);
        const host = links[0] ? new URL(links[0]).hostname.replace(/^www\./, "") : "";
        body = {
          mode: "motion",
          title: (words || host || "Motion graphics").slice(0, 120),
          prompt: d.prompt.trim(),
          options: { aspect: d.motion.aspect, duration_s: d.motion.durationTouched || !asked ? d.motion.duration_s : Math.min(60, Math.max(6, Math.round(asked))),
                     style: d.motion.style,
                     intro_template_id: d.introId, outro_template_id: d.outroId },
          source_urls: links,
          asset_ids: d.images.filter((i) => i.status === "ready").map((i) => i.asset.id),
          brand_authorized: d.authorized,
        };
      }
      const p = await api.createProject(body);
      Object.assign(d, { clips: [], images: [], links: [], prompt: "", poster: null, introId: null, outroId: null, menu: false, linkOpen: false });
      await refreshProjects();
      go({ view: "project", id: p.id });
    } catch (e) {
      toast(e.message, true);
      if (btn) btn.disabled = false;
    }
  }

  // =========================================================================== project views
  function statusPill(status) {
    const map = {
      ready: ["ok", "Analyzed"], done: ["ok", "Video ready"], review: ["ok", "Storyboard ready"],
      analyzing: ["accent", "Analyzing"], planning: ["accent", "Working"], rendering: ["accent", "Rendering"],
      failed: ["bad", "Needs attention"], partial: ["warn", "Needs attention"], empty: ["", "No clips"],
    };
    const [cls, label] = map[status] || ["", status];
    return `<span class="pill ${cls}"><span class="dot"></span>${label}</span>`;
  }

  const STAGE_LABEL = {
    hash: "reading file", probe: "reading file", starting: "starting", downloading: "fetching file",
    proxy: "making preview", signals: "analyzing frames and audio", saving: "saving", retrying: "retrying", queued: "waiting",
  };
  const stageLabel = (s) => STAGE_LABEL[s] || s || "waiting";

  function renderProject() {
    if (state.route.view !== "project") return;
    const view = $("#view");
    const p = state.project;
    if (!p) { view.innerHTML = `<div class="wrap"><div class="empty"><span class="spin"></span>Loading project…</div></div>`; return; }
    const scroll = window.scrollY;
    const active = document.activeElement;
    const activeId = active?.id;
    const sel = activeId && active.setSelectionRange ? [active.selectionStart, active.selectionEnd] : null;
    view.innerHTML = `<div class="wrap">${headHtml(p)}${p.mode === "motion" ? motionView(p) : editView(p)}</div>`;
    if (activeId) {
      const el = document.getElementById(activeId);
      if (el) { el.focus({ preventScroll: true }); if (sel && el.setSelectionRange) { try { el.setSelectionRange(sel[0], sel[1]); } catch { /* not a text field */ } } }
    }
    window.scrollTo(0, scroll);
    if (p.mode === "edit") {
      const clip = p.clips.find((c) => c.id === state.selectedClip);
      if (clip && clip.status === "ready" && $("#analysis")) loadAnalysis(clip);
      ensurePoster(p);
    }
  }

  function headHtml(p) {
    const confirmDel = state.confirm === "delete-project";
    return `
      <div class="proj-head">
        <div class="titles">
          <span class="eyebrow">${p.mode === "motion" ? "Motion graphics" : "Edit video"}</span>
          <h1>${esc(p.title || "Untitled project")}</h1>
          ${p.prompt ? `<div class="prompt-quote">“${esc(p.prompt)}”</div>` : ""}
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          ${statusPill(p.status)}
          ${p.mode === "edit" ? `<button type="button" class="btn small" data-action="add-to-project">${icon.plus} Add clip</button>` : ""}
          ${confirmDel
            ? `<span class="confirm">Delete this project and all its videos? <button type="button" class="btn small danger" data-action="delete-project-yes">Delete</button><button type="button" class="btn small ghost" data-action="cancel-confirm">Keep</button></span>`
            : `<button type="button" class="btn small ghost danger" data-action="delete-project">Delete</button>`}
        </div>
      </div>`;
  }

  // ---- edit ----------------------------------------------------------------------------------
  function editView(p) {
    const analyzing = p.clips.some((c) => c.status === "queued" || c.status === "processing");
    const anyReady = p.clips.some((c) => c.status === "ready");
    const latest = p.renders[0];
    const rendering = latest && (latest.status === "queued" || latest.status === "processing");
    const shown = p.renders.find((r) => r.id === state.ui.renderId && r.status === "ready") || p.renders.find((r) => r.status === "ready");
    let step = "";
    if (rendering) step = progressPanel("Making your edit", latest, ["Cutting dead air", "Reframing and grading", "Levelling the sound", "Rendering"]);
    else if (analyzing) step = `<section class="panel"><div class="panel-body"><div class="row"><span class="spin"></span><b>Analyzing your clips…</b></div>
        <span class="note">When every clip is done you can create your edit. You can leave this page; it keeps working.</span></div></section>`;
    else if (anyReady && (!shown || state.ui.showOptions)) step = editOptionsPanel(p);
    const failedRender = latest && latest.status === "failed" ? `<div class="errbox"><span>${esc(latest.error || "Rendering failed.")}</span>
        <button type="button" class="btn small" data-action="show-options">Try again</button></div>` : "";
    return `
      ${meterHtml(p.meter.total_raw_s, p.meter.limit_s, p.meter.clip_count)}
      ${failedRender}
      ${step}
      ${shown && !rendering ? resultPanel(p, shown) : ""}
      <section class="card" aria-label="Clips">
        <div class="card-head"><h2>Clips</h2><span class="muted" style="font-size:12px">In project order. Select one to see what Autocut found.</span></div>
        <div class="clip-rows">${p.clips.length ? p.clips.map((c, i) => clipRow(c, i, p.clips.length)).join("") : `<div class="empty">No clips yet. <button type="button" class="btn primary small" data-action="add-to-project">${icon.plus} Add clip</button></div>`}</div>
      </section>
      <details class="more" ${shown ? "" : "open"}><summary>What Autocut found in each clip</summary>
        <div id="analysis" style="margin-top:10px"></div></details>`;
  }

  function meterHtml(total, limit, count) {
    const over = total > limit + 0.01;
    return `
      <div class="meter ${over ? "over" : ""}">
        <div class="meter-row"><span><b class="mono" style="color:var(--fg)">${fmtDur(total)}</b> of ${fmtDur(limit)} total raw length</span><span>${count} / ${maxClips()} clips</span></div>
        <div class="meter-track"><div class="meter-fill" style="width:${Math.min(100, (total / limit) * 100).toFixed(1)}%"></div></div>
      </div>`;
  }

  function ensurePoster(p) {
    if (state.posters.has(p.id)) return;
    const clip = p.clips.find((c) => c.status === "ready" && c.proxy_url);
    if (!clip) return;
    state.posters.set(p.id, null);
    const v = document.createElement("video");
    v.muted = true; v.preload = "auto"; v.crossOrigin = "anonymous";
    v.onloadedmetadata = () => { v.currentTime = Math.min(2, (v.duration || 3) / 3); };
    v.onseeked = () => {
      try {
        const c = document.createElement("canvas");
        c.width = 240; c.height = Math.round(240 * v.videoHeight / v.videoWidth);
        c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
        state.posters.set(p.id, c.toDataURL("image/jpeg", 0.75));
        if (state.project?.id === p.id && $("[data-rgrade]")) renderProject();
      } catch { /* keep the illustration */ }
    };
    v.src = api.proxyUrl(clip);
  }

  function opt(key, fallback) {
    return state.ui.renderOpts[key] !== undefined ? state.ui.renderOpts[key] : fallback;
  }

  function templatePicker(prefix) {
    return myTemplateTiles(opt("intro_template_id", state.project?.options?.intro_template_id || null),
                           opt("outro_template_id", state.project?.options?.outro_template_id || null), prefix);
  }

  function editOptionsPanel(p) {
    const o = p.options || {};
    const aspect = opt("aspect", o.aspect || "9:16");
    const grade = opt("color_preset", o.color_preset || "natural");
    const target = opt("target_duration_s", o.target_duration_s ?? "");
    const res = opt("resolution", 720);
    return `
      <section class="panel cta-panel" aria-label="Create your edit">
        <div class="panel-head"><h2>Create your edit</h2><span class="sub">Cuts dead air, empty shots and long pauses, reframes around people, grades the color and levels the sound.</span></div>
        <div class="panel-body">
          <div class="row">
            <label class="field2" for="r-aspect">Format<select id="r-aspect">${Object.entries(ASPECT_LABEL).map(([k, l]) => `<option value="${k}" ${aspect === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
            <label class="field2" for="r-target">Length<select id="r-target"><option value="">Keep everything good</option>${[15, 30, 45, 60, 90, 120, 180].map((s) => `<option value="${s}" ${String(target) === String(s) ? "selected" : ""}>About ${fmtDur(s)}</option>`).join("")}</select></label>
            <label class="field2" for="r-res">Quality<select id="r-res"><option value="720" ${res === 720 ? "selected" : ""}>720p (faster)</option><option value="1080" ${res === 1080 ? "selected" : ""}>1080p</option></select></label>
          </div>
          <div class="trow"><div class="trow-head"><h3>Color grade</h3><span>Preview on your footage (approximate)</span></div>
            <div class="tiles">${colorTiles(grade, state.posters.get(p.id), "data-rgrade")}</div></div>
          ${templatePicker("r")}
          <div class="row"><button type="button" class="btn primary big-btn" data-action="render">${icon.wand} Create my edit</button>
            ${p.renders.some((r) => r.status === "ready") ? `<button type="button" class="btn ghost" data-action="hide-options">Cancel</button>` : ""}
            <span class="note">This first cut follows rules from the analysis. AI story editing and captions are coming next.</span></div>
        </div>
      </section>`;
  }

  function progressPanel(title, r, steps) {
    const f = r.progress || 0;
    const idx = Math.min(steps.length - 1, Math.floor(f * steps.length));
    return `
      <section class="panel" aria-label="${esc(title)}">
        <div class="panel-head"><span class="spin"></span><h2>${esc(title)}</h2><span class="sub">${pct(f)}${r.stage ? ` · ${esc(r.stage)}` : ""}</span></div>
        <div class="panel-body"><div class="progress"><i style="width:${(f * 100).toFixed(1)}%"></i></div>
          <div class="steps">${steps.map((s, i) => `<div class="step ${i < idx ? "done" : i === idx ? "active" : ""}">${i < idx ? icon.check : i === idx ? icon.dot : icon.clock}${esc(s)}</div>`).join("")}</div>
          <span class="note">You can leave this page. The video keeps rendering on the server.</span></div>
      </section>`;
  }

  function resultPanel(p, r) {
    const ready = p.renders.filter((x) => x.status === "ready");
    const s = r.settings || {};
    const markIn = state.ui.markIn, markOut = state.ui.markOut;
    let report = "";
    if (r.kind === "edit" && r.report) {
      const kept = r.report.segments || [];
      const removed = (r.report.removed || []).filter((x) => x.src_out - x.src_in >= 0.3);
      const clipName = (id) => p.clips.find((c) => c.id === id)?.filename || id;
      report = `<details class="more"><summary>Edit report: kept ${kept.length} part${kept.length === 1 ? "" : "s"}, cut ${removed.length}</summary>
        <div class="report findings">${[...kept.map((k) => ({ ...k, tag: "keep" })), ...removed.map((x) => ({ ...x, tag: "cut" }))]
          .sort((a, b) => (a.clip_id + a.src_in.toFixed(2).padStart(8, "0")).localeCompare(b.clip_id + b.src_in.toFixed(2).padStart(8, "0")))
          .map((x) => `<div class="finding" style="cursor:default"><span class="tc">${fmtTC(x.src_in)} – ${fmtTC(x.src_out)}</span>
            <span class="what"><b>${esc(x.reason || (x.tag === "keep" ? "Kept" : "Cut"))}</b> <span class="muted">· ${esc(clipName(x.clip_id))}</span></span>
            <span class="tag ${x.tag === "keep" ? "keep" : "cut"}">${x.tag === "keep" ? "Kept" : "Cut"}</span></div>`).join("")}</div></details>`;
    } else if (r.kind === "motion" && s.warnings?.length) {
      report = `<div class="banner">${icon.info}<span>Some text had to shrink a lot to fit (${esc(s.warnings.slice(0, 4).join(", "))}). Shorter wording will look better.</span></div>`;
    }
    const grade = presetByKey(s.color_preset);
    return `
      <section class="panel" aria-label="Your video">
        <div class="panel-head"><h2>Your video</h2><span class="sub">${fmtDur(r.duration_s)} · ${r.width}×${r.height}${r.kind === "edit" && grade ? ` · ${esc(grade.name)}` : ""}</span>
          <span class="spacer"></span>
          <a class="btn primary small" href="${esc(r.download_url)}" download>${icon.download} Download MP4</a>
          <button type="button" class="btn small" data-action="show-options">${r.kind === "edit" ? "Make another version" : "Render again"}</button></div>
        <div class="panel-body">
          <div class="result">
            <video id="result-video" src="${esc(r.video_url)}" ${r.poster_url ? `poster="${esc(r.poster_url)}"` : ""} controls playsinline preload="metadata"></video>
            <div class="side">
              <div class="savebox">
                <h4>Save part of this video as a template</h4>
                <span class="note">Play to the moment you want, then mark the start and end. Saved parts can open or close your future videos.</span>
                <div class="row">
                  <button type="button" class="btn small" data-action="mark-in">Start ${markIn != null ? `<span class="mono">${fmtTC(markIn)}</span>` : ""}</button>
                  <button type="button" class="btn small" data-action="mark-out">End ${markOut != null ? `<span class="mono">${fmtTC(markOut)}</span>` : ""}</button>
                  <button type="button" class="btn small ghost" data-action="mark-all">Whole video</button>
                </div>
                <div class="row">
                  <input id="tpl-name" type="text" placeholder="Name, e.g. Brand intro" maxlength="80" style="flex:1;min-width:140px;height:34px;border-radius:9px;border:1px solid var(--line);background:var(--surface-2);padding:0 10px">
                  <select id="tpl-kind" class="tselect" style="border-radius:9px"><option value="intro">Intro</option><option value="outro">Outro</option><option value="clip">Clip</option></select>
                  <button type="button" class="btn small primary" data-action="save-template" ${markIn != null && markOut != null && markOut > markIn ? "" : "disabled"}>Save</button>
                </div>
              </div>
              ${report}
              ${ready.length > 1 ? `<div class="trow"><div class="trow-head"><h3>Versions</h3></div><div class="renders-list">${ready.map((x, i) =>
                `<button type="button" data-render="${x.id}" aria-current="${x.id === r.id}">${x.poster_url ? `<img src="${esc(x.poster_url)}" alt="">` : ""}<span>${i === 0 ? "Latest" : `v${ready.length - i}`} · ${fmtDur(x.duration_s)}</span></button>`).join("")}</div></div>` : ""}
            </div>
          </div>
        </div>
      </section>`;
  }

  function clipRow(c, i, n) {
    const m = c.media;
    const confirm = state.confirm === `remove-${c.id}`;
    let status;
    if (c.status === "ready") status = `<span class="pill ok"><span class="dot"></span>Ready</span>`;
    else if (c.status === "failed") status = `<span class="pill bad"><span class="dot"></span>Failed</span>`;
    else status = `<span class="pill accent"><span class="dot"></span>${c.status === "queued" ? (c.stage === "retrying" ? "Retrying" : "Queued") : "Analyzing"}</span>`;
    const thumb = c.status === "ready" && api.proxyUrl(c) ? `<video src="${esc(api.proxyUrl(c))}#t=${(c.duration_s * 0.4).toFixed(1)}" muted preload="metadata" playsinline></video>` : "";
    return `
      <div class="clip-row" role="button" tabindex="0" data-select="${esc(c.id)}" aria-current="${state.selectedClip === c.id}">
        <span class="mono muted" style="text-align:center">${i + 1}</span>
        <div class="thumb">${thumb}<span class="dur">${fmtDur(c.duration_s)}</span></div>
        <div class="info">
          <span class="fname" title="${esc(c.filename)}">${esc(c.filename)}</span>
          <span class="facts">${status}
            ${m ? `<span>${m.display_width}×${m.display_height}</span>${m.fps ? `<span>${Math.round(m.fps)} fps</span>` : ""}${m.has_audio == null ? "" : `<span>${m.has_audio ? "audio" : "no audio"}</span>`}` : ""}
            ${c.status === "processing" || c.status === "queued" ? `<span class="mono">${pct(c.progress)} · ${esc(stageLabel(c.stage))}</span>` : ""}
            ${c.status === "failed" ? `<span style="color:var(--bad)">${esc(c.error || c.error_code)}</span>` : ""}</span>
          ${c.status === "processing" || c.status === "queued" ? `<div class="progress"><i style="width:${(c.progress * 100).toFixed(1)}%"></i></div>` : ""}
        </div>
        <div class="row-actions">
          ${c.status === "failed" ? `<button type="button" class="btn icon ghost" data-retry="${esc(c.id)}" aria-label="Retry">${icon.retry}</button>` : ""}
          <button type="button" class="btn icon ghost" data-pmove="${esc(c.id)}:-1" aria-label="Move up" ${i === 0 ? "disabled" : ""}>${icon.up}</button>
          <button type="button" class="btn icon ghost" data-pmove="${esc(c.id)}:1" aria-label="Move down" ${i === n - 1 ? "disabled" : ""}>${icon.down}</button>
          ${confirm ? `<button type="button" class="btn small danger" data-premove-yes="${esc(c.id)}">Remove</button>`
            : `<button type="button" class="btn icon ghost" data-premove="${esc(c.id)}" aria-label="Remove clip">${icon.x}</button>`}
        </div>
      </div>`;
  }

  // ---- motion --------------------------------------------------------------------------------
  function motionView(p) {
    const m = p.motion;
    if (!m) return `<div class="empty">Preparing…</div>`;
    const latest = p.renders[0];
    const rendering = latest && (latest.status === "queued" || latest.status === "processing");
    const shown = p.renders.find((r) => r.id === state.ui.renderId && r.status === "ready") || p.renders.find((r) => r.status === "ready");
    if (m.status === "queued" || m.status === "planning") {
      const steps = ["Reading your sources", "Building your brand kit", "Writing your storyboard"];
      const idx = Math.max(0, steps.indexOf(m.stage));
      return `<section class="panel"><div class="panel-head"><span class="spin"></span><h2>Preparing your storyboard</h2><span class="sub">Usually under a minute</span></div>
        <div class="panel-body"><div class="steps">${steps.map((s, i) => `<div class="step ${i < idx ? "done" : i === idx ? "active" : ""}">${i < idx ? icon.check : i === idx ? icon.dot : icon.clock}${s}</div>`).join("")}</div>
        <span class="note">Claude only uses facts from your links, files and prompt. Every claim is tagged with where it came from.</span></div></section>`;
    }
    if (m.status === "failed") {
      return `<div class="errbox"><span><b>The storyboard could not be made.</b> ${esc(m.error || "")}</span>
        <button type="button" class="btn small" data-action="retry-plan">${icon.retry} Try again</button></div>
        ${m.brand_kit ? brandKitPanel(p) : ""}`;
    }
    const failedRender = latest && latest.status === "failed" ? `<div class="errbox"><span>${esc(latest.error || "Rendering failed.")}</span></div>` : "";
    return `
      ${rendering ? progressPanel("Animating your video", latest, ["Setting up scenes", "Animating frames", "Encoding", "Finishing"]) : ""}
      ${failedRender}
      ${shown && !rendering && !state.ui.showOptions ? resultPanel(p, shown) : ""}
      ${m.error && !m.pending_scene ? `<div class="errbox"><span>${esc(m.error)}</span></div>` : ""}
      ${brandKitPanel(p)}
      ${storyboardPanel(p)}
      ${!rendering ? motionRenderPanel(p) : ""}
      ${state.sb?.dirty ? `<div class="savebar"><span>You have unsaved storyboard changes</span><span class="row"><button type="button" class="btn small ghost" style="color:inherit" data-action="discard-sb">Discard</button><button type="button" class="btn small" data-action="save-sb">Save changes</button></span></div>` : ""}`;
  }

  function assetById(p, id) { return p.motion.assets.find((a) => a.id === id); }

  function brandKitPanel(p) {
    const kit = p.motion.brand_kit || {};
    const colors = (state.sb?.comp?.brand?.colors) || kit.colors || {};
    const logos = p.motion.assets.filter((a) => a.kind === "logo" || a.kind === "photo");
    const logoId = state.sb?.comp?.brand?.logo_asset ?? kit.logo_asset;
    const names = { primary: "Main", secondary: "Second", accent: "Accent", background: "Background", text: "Text" };
    return `
      <section class="panel" aria-label="Brand kit">
        <div class="panel-head"><h2>Brand kit</h2><span class="sub">${kit.sources?.length ? `From ${kit.sources.map((s) => esc(s.url.replace(/^https?:\/\//, ""))).join(", ")}` : "From your files and prompt"}</span></div>
        <div class="panel-body">
          ${(kit.warnings || []).map((w) => `<div class="banner">${icon.info}<span>${esc(w)}</span></div>`).join("")}
          <div class="brandkit">
            <label class="field2" for="bk-name">Brand name<input id="bk-name" type="text" value="${esc(state.sb?.comp?.brand?.name ?? kit.name ?? "")}" maxlength="120"></label>
            <div class="field2">Colors<div class="swatches">${Object.keys(names).map((k) => `<label class="swatch" for="bk-${k}"><input id="bk-${k}" type="color" data-bk-color="${k}" value="${esc((colors[k] || "#888888").slice(0, 7))}">${names[k]}</label>`).join("")}</div></div>
            ${logos.length ? `<div class="field2">Logo<div class="logo-pick">${logos.map((a) => `<button type="button" data-bk-logo="${a.id}" aria-pressed="${logoId === a.id}" style="background-image:url(${esc(a.url)})" title="${esc(a.source)}"></button>`).join("")}
              <button type="button" data-bk-logo="" aria-pressed="${!logoId}" style="background:var(--surface-2);font-size:11px;color:var(--muted)">Name only</button></div></div>` : ""}
          </div>
        </div>
      </section>`;
  }

  function sceneFieldsHtml(sc, i) {
    const c = sc.content || {};
    const f = (field, label, value, multi = false, max = 200) => `<label for="sf-${i}-${field}">${label}${multi
      ? `<textarea id="sf-${i}-${field}" data-sf="${i}:${field}" rows="3">${esc(value)}</textarea>`
      : `<input id="sf-${i}-${field}" data-sf="${i}:${field}" type="text" maxlength="${max}" value="${esc(value)}">`}</label>`;
    const t = sc.template;
    const out = [];
    if (!["logo_sting", "testimonial_card", "cta_end_card"].includes(t)) out.push(f("kicker", "Small line above", c.kicker || "", false, 60));
    out.push(f("headline", t === "testimonial_card" ? "Quote" : t === "logo_sting" ? "Tagline" : "Headline", c.headline || "", t === "testimonial_card", 200));
    if (t === "price_offer") out.push(f("price", "Price", c.price || "", false, 40));
    if (t === "stats_counter") out.push(f("stats", "Numbers (one per line: value | label)", (c.stats || []).map((s) => `${s.value} | ${s.label}`).join("\n"), true));
    if (t === "feature_list" || t === "price_offer") out.push(f("items", t === "price_offer" ? "Badges (one per line)" : "Points (one per line)", (c.items || []).join("\n"), true));
    if (t === "cta_end_card") out.push(f("cta", "Button text", c.cta || "", false, 40));
    if (t !== "logo_sting") out.push(f("subtext", t === "testimonial_card" ? "Who said it" : t === "cta_end_card" ? "Website or contact" : "Supporting line", c.subtext || "", false, 500));
    return out.join("");
  }

  function storyboardPanel(p) {
    const sb = state.sb;
    if (!sb) return "";
    const comp = sb.comp;
    const pending = p.motion.pending_scene;
    const imgs = p.motion.assets.filter((a) => a.kind !== "logo");
    return `
      <section class="panel" aria-label="Storyboard">
        <div class="panel-head"><h2>Storyboard</h2><span class="sub">${comp.scenes.length} scenes · ${comp.meta.duration_s}s · edit anything, or ask Claude to rewrite a scene</span></div>
        <div class="panel-body">
          ${comp.meta.summary ? `<div class="note">${esc(comp.meta.summary)}</div>` : ""}
          <div class="scenes">${comp.scenes.map((sc, i) => `
            <div class="scene-card ${pending === sc.id ? "busy" : ""}">
              <div class="scene-top"><span class="num">${i + 1}</span>
                <select data-stpl="${i}" aria-label="Scene type">${Object.entries(TEMPLATE_LABEL).filter(([k]) => k !== "footage").map(([k, l]) => `<option value="${k}" ${sc.template === k ? "selected" : ""}>${l}</option>`).join("")}</select>
                <span class="tc mono">${sc.t_in.toFixed(1)}s – ${sc.t_out.toFixed(1)}s</span>
                ${pending === sc.id ? `<span class="row" style="gap:6px"><span class="spin"></span><span class="note">Claude is rewriting this scene…</span></span>` : ""}
              </div>
              <div class="scene-body">
                <div class="scene-fields">${sceneFieldsHtml(sc, i)}</div>
                <div class="scene-side">
                  ${["product_reveal", "device_mockup"].includes(sc.template) ? `<span>Image</span><div class="imgs">${imgs.map((a) => `<button type="button" data-simg="${i}:${a.id}" aria-pressed="${(sc.content.assets || [])[0] === a.id}" style="background-image:url(${esc(a.url)})" title="${esc(a.source)}"></button>`).join("") || "<span>No images. Add photos when you create a project.</span>"}</div>` : ""}
                  ${sc.claims?.length ? `<span>Facts and their sources</span><div class="claims">${sc.claims.map((c) => `<span class="claim" title="${esc(c.text)}">${esc(c.source)}</span>`).join("")}</div>` : ""}
                  ${sc.reason ? `<span class="reason">${esc(sc.reason)}</span>` : ""}
                </div>
              </div>
              <div class="regen"><input id="regen-${esc(sc.id)}" type="text" placeholder="Ask Claude to change this scene, e.g. make it punchier" maxlength="300" ${pending ? "disabled" : ""}>
                <button type="button" class="btn small" data-regen="${esc(sc.id)}" ${pending ? "disabled" : ""}>${icon.wand} Rewrite</button></div>
            </div>`).join("")}</div>
        </div>
      </section>`;
  }

  function motionRenderPanel(p) {
    const comp = state.sb?.comp;
    if (!comp) return "";
    const shown = p.renders.some((r) => r.status === "ready");
    if (shown && !state.ui.showOptions) return "";
    const res = opt("resolution", 720);
    return `
      <section class="panel cta-panel" aria-label="Create video">
        <div class="panel-head"><h2>${shown ? "Render again" : "Happy with the storyboard?"}</h2><span class="sub">Pick the look and format, then create the video.</span></div>
        <div class="panel-body">
          <div class="trow"><div class="trow-head"><h3>Motion style</h3></div><div class="tiles">${styleTiles(comp.meta.style, "data-sbstyle")}</div></div>
          <div class="row">
            <label class="field2" for="sb-aspect">Format<select id="sb-aspect">${Object.entries(ASPECT_LABEL).map(([k, l]) => `<option value="${k}" ${comp.meta.aspect === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
            <label class="field2" for="r-res">Quality<select id="r-res"><option value="720" ${res === 720 ? "selected" : ""}>720p (faster)</option><option value="1080" ${res === 1080 ? "selected" : ""}>1080p</option></select></label>
          </div>
          ${templatePicker("r")}
          <div class="row"><button type="button" class="btn primary big-btn" data-action="render" ${p.motion.pending_scene ? "disabled" : ""}>${icon.wand} Create video</button>
            ${shown ? `<button type="button" class="btn ghost" data-action="hide-options">Cancel</button>` : ""}
            <span class="note">Silent for now: voiceover and music are on the way.</span></div>
        </div>
      </section>`;
  }

  async function saveStoryboard() {
    const p = state.project, sb = state.sb;
    if (!p || !sb || !sb.dirty) return true;
    try {
      const r = await api.putComposition(p.id, sb.comp);
      sb.version = r.version;
      sb.comp = clone(r.composition);
      sb.dirty = false;
      applySnapshot(await api.getProject(p.id));
      return true;
    } catch (e) {
      toast(e.details?.feedback ? `${e.message}: ${String(e.details.feedback).split("\n").slice(1, 3).join(" ")}` : e.message, true);
      return false;
    }
  }

  async function startRender() {
    const p = state.project;
    if (!p) return;
    if (p.mode === "motion" && !(await saveStoryboard())) return;
    const o = p.options || {};
    const body = {
      resolution: Number(opt("resolution", 720)),
      intro_template_id: opt("intro_template_id", o.intro_template_id || null) || "",
      outro_template_id: opt("outro_template_id", o.outro_template_id || null) || "",
    };
    if (p.mode === "edit") {
      body.aspect = opt("aspect", o.aspect || "9:16");
      body.color_preset = opt("color_preset", o.color_preset || "natural");
      const t = opt("target_duration_s", o.target_duration_s ?? "");
      if (t) body.target_duration_s = Number(t);
    }
    try {
      await api.createRender(p.id, body);
      state.ui.showOptions = false;
      applySnapshot(await api.getProject(p.id));
      startWatch(p.id);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) { toast(e.message, true); }
  }
  // ---- analysis -------------------------------------------------------------------------------
  async function loadAnalysis(clip) {
    let sig = state.signals.get(clip.id);
    if (!sig) {
      try { sig = await api.signals(clip); state.signals.set(clip.id, sig); }
      catch (e) { $("#analysis").innerHTML = `<div class="banner">${icon.info}<span>${esc(e.message)}</span></div>`; return; }
    }
    if (state.selectedClip !== clip.id || state.route.view !== "project") return;
    if (!sig) return renderPreviewOnly(clip);
    renderAnalysis(clip, sig);
  }

  function mergeSeconds(rows, pred) {
    const out = [];
    for (const r of rows) {
      if (!pred(r)) continue;
      const last = out[out.length - 1];
      if (last && last[1] >= r.t) last[1] = r.t + 1; else out.push([r.t, r.t + 1]);
    }
    return out.map(([a, b]) => ({ t_in: a, t_out: b }));
  }

  function renderAnalysis(clip, s) {
    const D = s.media.duration_s;
    const x = (t) => `${((t / D) * 100).toFixed(3)}%`;
    const w = (a, b) => `${(((b - a) / D) * 100).toFixed(3)}%`;
    const segs = (ranges, color, title) => ranges.map((r) =>
      `<span class="seg" style="left:${x(r.t_in)};width:${w(r.t_in, r.t_out)};background:${color}" title="${esc(title(r))}"></span>`).join("");
    const ps = s.per_second;
    const hasAudio = !!s.summary.audio;
    const area = (vals, max, color) => {
      const n = vals.length;
      if (!n) return "";
      const pts = vals.map((v, i) => `${((i + 0.5) / n * 100).toFixed(2)},${(100 - Math.min(1, v / max) * 92).toFixed(2)}`);
      return `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <polygon points="0,100 ${pts.join(" ")} 100,100" fill="${color}" fill-opacity="0.22"></polygon>
        <polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"></polyline></svg>`;
    };
    const maxMotion = Math.max(0.02, ...ps.map((r) => r.motion));
    const faces = mergeSeconds(ps, (r) => r.faces.length > 0 || r.person_count > 0);
    const cut = s.dead_zones.filter((z) => z.reason !== "static_subject_hold");
    const hold = s.dead_zones.filter((z) => z.reason === "static_subject_hold");
    const step = D > 600 ? 120 : D > 240 ? 60 : D > 90 ? 15 : D > 40 ? 10 : 5;
    const ticks = [];
    for (let t = 0; t <= D + 0.001; t += step) ticks.push(t);

    const tracks = [
      ["Scenes", `<div class="lane" data-lane>${s.scene_cuts.map((c) => `<span class="tick" style="left:${x(c)}" title="Cut at ${fmtTC(c)}"></span>`).join("")}</div>`],
      ["Motion", `<div class="lane tall" data-lane>${area(ps.map((r) => r.motion), maxMotion, "var(--accent)")}</div>`],
    ];
    if (hasAudio) {
      tracks.push(["Loudness", `<div class="lane tall" data-lane>${area(ps.map((r) => Math.max(0, (r.loudness_lufs ?? -70) + 60)), 60, "var(--t-speech)")}</div>`]);
      tracks.push(["Speech", `<div class="lane" data-lane>${segs(s.speech_ranges, "var(--t-speech)", (r) => `Speech ${fmtTC(r.t_in)}–${fmtTC(r.t_out)}`)}</div>`]);
      tracks.push(["Music", `<div class="lane" data-lane>${segs(s.music_ranges, "var(--t-music)", (r) => `Music ${fmtTC(r.t_in)}–${fmtTC(r.t_out)}`)}${s.beats.map((b) => `<span class="beat" style="left:${x(b)}"></span>`).join("")}</div>`]);
      tracks.push(["Silence", `<div class="lane" data-lane>${segs(s.silences, "var(--t-silence)", (r) => `Silence ${fmtTC(r.t_in)}–${fmtTC(r.t_out)}`)}</div>`]);
    }
    tracks.push(["People", `<div class="lane" data-lane>${segs(faces, "var(--t-face)", (r) => `Person visible ${fmtTC(r.t_in)}–${fmtTC(r.t_out)}`)}</div>`]);
    tracks.push(["Dead air", `<div class="lane" data-lane>${segs(cut, "var(--t-dead)", (r) => `${labelFor(r.reason)} ${fmtTC(r.t_in)}–${fmtTC(r.t_out)}`)}${segs(hold, "var(--t-hold)", (r) => `Person holding still ${fmtTC(r.t_in)}–${fmtTC(r.t_out)}`)}</div>`]);
    tracks.push(["Quality", `<div class="lane" data-lane>${segs(s.quality_flags, "var(--t-flag)", (r) => `${labelFor(r.reason)} ${fmtTC(r.t_in)}–${fmtTC(r.t_out)}`)}</div>`]);

    const findings = [
      ...s.dead_zones.map((z) => ({ t_in: z.t_in, t_out: z.t_out, tag: z.reason === "static_subject_hold" ? "look" : "cut", what: labelFor(z.reason), detail: z.reason === "static_subject_hold" ? "Person on screen but nothing changes. Kept for review." : z.reason === "black_frames" ? "Black screen." : "No motion and nobody speaking." })),
      ...s.quality_flags.filter((f) => f.reason !== "black").map((f) => ({ t_in: f.t_in, t_out: f.t_out, tag: "look", what: labelFor(f.reason), detail: qualityDetail(f.reason) })),
    ].sort((a, b) => a.t_in - b.t_in);

    const a = s.summary.audio, v = s.summary.video;
    $("#analysis").innerHTML = `
      <section class="card" aria-label="Analysis">
        <div class="card-head">
          <h2>What Autocut found</h2>
          <span class="muted" style="font-size:12px">${esc(clip.filename)} · ${fmtDur(D)} · analyzed in ${(Object.values(s.analysis.timings_s).reduce((m, x) => Math.max(m, x), 0)).toFixed(1)} s</span>
          <span class="spacer"></span>
          ${DEMO ? "" : `<a class="btn small ghost" href="${esc(api.signalsDownloadUrl(clip))}" target="_blank" rel="noopener">signals.json</a>`}
        </div>
        <div class="card-body" style="display:flex;flex-direction:column;gap:16px">
          <div class="viewer">
            <div class="player" id="player">
              <video id="video" src="${esc(api.proxyUrl(clip))}" controls playsinline preload="metadata"></video>
              <span class="tc-overlay" id="tc">00:00.0</span>
              <span class="subject-box" id="subject-box" hidden></span>
            </div>
            <div class="stats">
              <div class="stat"><span class="k">Dead air</span><span class="v">${pct(s.summary.dead_ratio)}</span><span class="s">${fmtDur(s.summary.dead_seconds)} likely trimmed or reviewed</span></div>
              <div class="stat"><span class="k">Usable footage</span><span class="v">${fmtDur(s.summary.usable_seconds)}</span><span class="s">of ${fmtDur(D)}</span></div>
              <div class="stat"><span class="k">Scenes</span><span class="v">${v.scene_count}</span><span class="s">${s.scene_cuts.length} hard cut${s.scene_cuts.length === 1 ? "" : "s"}</span></div>
              <div class="stat"><span class="k">People on screen</span><span class="v">${pct(v.person_ratio)}</span><span class="s">faces ${pct(v.face_ratio)} of the time</span></div>
              <div class="stat"><span class="k">Speech</span><span class="v">${a ? pct(a.speech_ratio) : "–"}</span><span class="s">${a ? (a.music_ratio ? `music ${pct(a.music_ratio)}${a.tempo_bpm ? ` · ${Math.round(a.tempo_bpm)} BPM` : ""}` : "no music") : "no audio track"}</span></div>
              <div class="stat"><span class="k">Loudness</span><span class="v mono">${a && a.integrated_lufs != null ? `${a.integrated_lufs.toFixed(0)}` : "–"}<small style="font-size:12px;color:var(--muted)"> LUFS</small></span><span class="s">${a ? (a.integrated_lufs != null ? "normalized to −14 at export" : "track is silent") : "no audio track"}</span></div>
            </div>
          </div>
          <div class="timeline" id="timeline">
            <div class="ruler">${ticks.map((t) => `<span style="left:${x(t)}">${fmtDur(t)}</span>`).join("")}</div>
            ${tracks.map(([n, lane]) => `<div class="track"><span class="tname">${n}</span>${lane}</div>`).join("")}
            <div class="playhead-wrap"><span class="playhead" id="playhead" style="left:0"></span></div>
          </div>
          <div class="legend">
            <span><i style="background:var(--t-speech)"></i>Speech</span>
            <span><i style="background:var(--t-music)"></i>Music and beats</span>
            <span><i style="background:var(--t-face)"></i>Person visible</span>
            <span><i style="background:var(--t-dead)"></i>Dead air (cut candidate)</span>
            <span><i style="background:var(--t-hold)"></i>Person holding still</span>
            <span><i style="background:var(--t-flag)"></i>Blur, shake or exposure</span>
          </div>
        </div>
      </section>
      <section class="card" aria-label="Findings">
        <div class="card-head"><h2>Moments to cut or check</h2><span class="muted" style="font-size:12px">Select one to jump there. The edit itself arrives in the next updates.</span></div>
        <div class="findings">
          ${findings.length ? findings.map((f) => `
            <button type="button" class="finding" data-seek="${f.t_in}">
              <span class="tc">${fmtTC(f.t_in)} – ${fmtTC(f.t_out)}</span>
              <span class="what"><b>${esc(f.what)}</b> <span class="muted">· ${esc(f.detail)}</span></span>
              <span class="tag ${f.tag}">${f.tag === "cut" ? "Likely cut" : "Review"}</span>
            </button>`).join("") : `<div class="empty">Clean clip: no dead air or quality problems found.</div>`}
        </div>
      </section>`;
    bindPlayer(s);
  }

  /** Hosted preview only: the clip plays, but there is no server here to analyze it. */
  function renderPreviewOnly(clip) {
    $("#analysis").innerHTML = `
      <section class="card" aria-label="Preview">
        <div class="card-head"><h2>Your clip</h2><span class="muted" style="font-size:12px">${esc(clip.filename)} · ${fmtDur(clip.duration_s)}</span></div>
        <div class="card-body" style="display:flex;flex-direction:column;gap:14px">
          <div class="player" style="max-width:720px"><video src="${esc(api.proxyUrl(clip))}" controls playsinline preload="metadata"></video></div>
          <div class="banner">${icon.info}<span>This hosted preview has no analysis server, so it can't read your footage. Run Autocut on your machine (one command, see the README) to get the full timeline for your own clips. Open <b>Sample project</b> to see what the analysis looks like.</span></div>
        </div>
      </section>`;
  }

  function labelFor(reason) {
    return ({
      black_frames: "Black frames", static_empty: "Empty, static shot", static_subject_hold: "Person holding still",
      blur: "Out of focus", shake: "Camera shake", black: "Black frames", underexposed: "Too dark", overexposed: "Too bright",
    })[reason] || reason;
  }
  function qualityDetail(reason) {
    return ({ blur: "Softer than the rest of the clip.", shake: "Hand-held jitter; stabilize or cut.", underexposed: "Exposure will be lifted in grading.", overexposed: "Highlights are clipped." })[reason] || "";
  }

  function bindPlayer(s) {
    const video = $("#video"), playhead = $("#playhead"), tc = $("#tc"), box = $("#subject-box");
    if (!video) return;
    const D = s.media.duration_s;
    const update = () => {
      const t = video.currentTime || 0;
      playhead.style.left = `${Math.min(100, (t / D) * 100)}%`;
      tc.textContent = fmtTC(t);
      const row = s.per_second[Math.min(s.per_second.length - 1, Math.floor(t))];
      const sb = row?.subject;
      if (sb && video.videoWidth) {
        // Map the normalised subject box onto the letterboxed picture inside the <video> element.
        const vw = video.clientWidth, vh = video.clientHeight, ar = video.videoWidth / video.videoHeight;
        let pw = vw, ph = vw / ar;
        if (ph > vh) { ph = vh; pw = vh * ar; }
        const ox = (vw - pw) / 2, oy = (vh - ph) / 2;
        Object.assign(box.style, { left: `${ox + sb.x * pw}px`, top: `${oy + sb.y * ph}px`, width: `${sb.w * pw}px`, height: `${sb.h * ph}px` });
        box.hidden = false;
      } else box.hidden = true;
    };
    let raf = 0;
    const loop = () => { update(); if (!video.paused) raf = requestAnimationFrame(loop); };
    video.addEventListener("play", () => { cancelAnimationFrame(raf); loop(); });
    video.addEventListener("pause", update);
    video.addEventListener("seeked", update);
    video.addEventListener("loadedmetadata", update);
    $("#timeline").addEventListener("pointerdown", (e) => {
      const lane = e.target.closest("[data-lane]");
      if (!lane) return;
      const r = lane.getBoundingClientRect();
      video.currentTime = Math.max(0, Math.min(D, ((e.clientX - r.left) / r.width) * D));
      update();
    });
  }


  // ===================================================================================== events
  function markDirty(rerender = false) {
    const was = state.sb.dirty;
    state.sb.dirty = true;
    if (!was || rerender) renderProject();
  }

  function setSceneField(i, field, value) {
    const sc = state.sb.comp.scenes[i];
    sc.content = sc.content || {};
    if (field === "items") sc.content.items = value.split("\n").map((s) => s.trim()).filter(Boolean).slice(0, 5);
    else if (field === "stats") {
      sc.content.stats = value.split("\n").map((l) => l.trim()).filter(Boolean).slice(0, 3).map((l) => {
        const [v, ...rest] = l.split("|");
        return { value: (v || "").trim().slice(0, 24) || "0", label: rest.join("|").trim().slice(0, 60) };
      });
    } else sc.content[field] = value;
  }

  async function regenerate(sid) {
    const p = state.project;
    const note = ($(`#regen-${CSS.escape(sid)}`)?.value || "").trim();
    if (state.sb?.dirty && !(await saveStoryboard())) return;
    try {
      await api.regenerate(p.id, sid, note);
      applySnapshot(await api.getProject(p.id));
      startWatch(p.id);
    } catch (e) { toast(e.message, true); }
  }

  async function saveTemplate() {
    const p = state.project;
    const r = p.renders.find((x) => x.id === state.ui.renderId && x.status === "ready") || p.renders.find((x) => x.status === "ready");
    const name = ($("#tpl-name")?.value || "").trim() || `${p.title || "Autocut"} ${$("#tpl-kind")?.value || "clip"}`;
    try {
      await api.saveTemplate({ render_id: r.id, t_in: state.ui.markIn, t_out: state.ui.markOut, name: name.slice(0, 80), kind: $("#tpl-kind")?.value || "clip" });
      await refreshTemplates();
      state.ui.markIn = state.ui.markOut = null;
      toast(`Saved “${name}” to My templates`);
      renderProject();
    } catch (e) { toast(e.message, true); }
  }

  function toggleTemplate(target, slot, id) {
    if (target === "d") {
      const key = slot === "intro" ? "introId" : "outroId";
      state.draft[key] = state.draft[key] === id ? null : id;
      rerenderComposer();
    } else {
      const key = `${slot}_template_id`;
      const cur = opt(key, state.project?.options?.[key] || null);
      state.ui.renderOpts[key] = cur === id ? "" : id;
      renderProject();
    }
  }

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-select], a");
    if (!t) {
      if (state.draft.menu && !e.target.closest(".menu")) { state.draft.menu = false; rerenderComposer(); }
      return;
    }
    const d = t.dataset;
    const p = state.project;
    if (t.id === "new-project") return go({ view: "new" });
    if (d.open) return go({ view: "project", id: d.open });

    // ---- composer
    if (d.mode) { state.draft.mode = d.mode; state.draft.menu = false; return rerenderComposer(); }
    if (d.example) { state.draft.prompt = d.example; rerenderComposer(); $("#prompt").focus(); return; }
    if (d.action === "menu") { state.draft.menu = !state.draft.menu; return rerenderComposer(); }
    if (d.action === "pick-video") { state.draft.menu = false; rerenderComposer(); const fi = $("#file-input"); fi.dataset.target = ""; fi.click(); return; }
    if (d.action === "pick-image") { state.draft.menu = false; rerenderComposer(); $("#image-input").click(); return; }
    if (d.action === "open-link") { state.draft.menu = false; state.draft.linkOpen = true; rerenderComposer(); $("#link-input")?.focus(); return; }
    if (d.action === "close-link") { state.draft.linkOpen = false; return rerenderComposer(); }
    if (d.action === "add-link") return addLink();
    if (d.action === "send") return send();
    if (d.rmClip) {
      const c = state.draft.clips.find((x) => x.key === d.rmClip);
      c?.ctrl?.abort();
      state.draft.clips = state.draft.clips.filter((x) => x.key !== d.rmClip);
      for (const x of state.draft.clips) if (x.status === "error" && x.upload && draftTotal() <= limitS() + 0.01) { x.status = "ready"; x.error = null; }
      if (!state.draft.clips.length) state.draft.poster = null;
      return rerenderComposer();
    }
    if (d.rmImg) { state.draft.images = state.draft.images.filter((x) => x.key !== d.rmImg); return rerenderComposer(); }
    if (d.rmLink) { state.draft.links = state.draft.links.filter((x) => x !== d.rmLink); return rerenderComposer(); }
    if (d.grade) { state.draft.edit.color_preset = d.grade; return rerenderComposer(); }
    if (d.style) { state.draft.motion.style = d.style; return rerenderComposer(); }
    if (d.dIntro) return toggleTemplate("d", "intro", d.dIntro);
    if (d.dOutro) return toggleTemplate("d", "outro", d.dOutro);

    // ---- project: shared
    if (d.rIntro) return toggleTemplate("r", "intro", d.rIntro);
    if (d.rOutro) return toggleTemplate("r", "outro", d.rOutro);
    if (d.rgrade) { state.ui.renderOpts.color_preset = d.rgrade; return renderProject(); }
    if (d.action === "render") { t.disabled = true; return startRender(); }
    if (d.action === "show-options") { state.ui.showOptions = true; renderProject(); return; }
    if (d.action === "hide-options") { state.ui.showOptions = false; return renderProject(); }
    if (d.render) { state.ui.renderId = d.render; state.ui.markIn = state.ui.markOut = null; return renderProject(); }
    if (d.action === "mark-in" || d.action === "mark-out" || d.action === "mark-all") {
      const v = $("#result-video");
      if (!v) return;
      if (d.action === "mark-all") { state.ui.markIn = 0; state.ui.markOut = v.duration || 0; }
      else state.ui[d.action === "mark-in" ? "markIn" : "markOut"] = Math.round(v.currentTime * 10) / 10;
      const keepTime = v.currentTime, wasPaused = v.paused;
      renderProject();
      const nv = $("#result-video");
      if (nv) { nv.currentTime = keepTime; if (!wasPaused) nv.play().catch(() => {}); }
      return;
    }
    if (d.action === "save-template") return saveTemplate();
    if (d.action === "add-to-project") { const fi = $("#file-input"); fi.dataset.target = p.id; fi.click(); return; }
    if (d.action === "delete-project") { state.confirm = "delete-project"; return renderProject(); }
    if (d.action === "cancel-confirm") { state.confirm = null; return renderProject(); }
    if (d.action === "delete-project-yes" && p) {
      try { await api.deleteProject(p.id); toast("Project deleted"); await refreshProjects(); go({ view: "new" }); } catch (err) { toast(err.message, true); }
      return;
    }

    // ---- motion
    if (d.action === "retry-plan" && p) {
      try { await api.retryPlan(p.id); applySnapshot(await api.getProject(p.id)); startWatch(p.id); } catch (err) { toast(err.message, true); }
      return;
    }
    if (d.regen) return regenerate(d.regen);
    if (d.action === "save-sb") { if (await saveStoryboard()) toast("Storyboard saved"); return; }
    if (d.action === "discard-sb") { state.sb = { pid: p.id, version: p.motion.version, comp: clone(p.motion.composition), dirty: false }; return renderProject(); }
    if (d.bkLogo !== undefined && state.sb) { state.sb.comp.brand.logo_asset = d.bkLogo || null; return markDirty(true); }
    if (d.simg && state.sb) {
      const [i, aid] = d.simg.split(":");
      state.sb.comp.scenes[Number(i)].content.assets = [aid];
      return markDirty(true);
    }
    if (d.sbstyle && state.sb) { state.sb.comp.meta.style = d.sbstyle; return markDirty(true); }

    // ---- edit
    if (d.select && !t.matches("button")) {
      if (e.target.closest("button")) return;
      state.selectedClip = d.select;
      const det = $("details.more"); if (det) det.open = true;
      return renderProject();
    }
    if (d.pmove && p) {
      const [cid, dir] = d.pmove.split(":");
      const ids = p.clips.map((c) => c.id), i = ids.indexOf(cid), j = i + Number(dir);
      if (j < 0 || j >= ids.length) return;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      try { applySnapshot(await api.reorder(p.id, ids)); } catch (err) { toast(err.message, true); }
      return;
    }
    if (d.premove) { state.confirm = `remove-${d.premove}`; return renderProject(); }
    if (d.premoveYes && p) {
      try {
        await api.removeClip(p.id, d.premoveYes);
        state.signals.delete(d.premoveYes);
        state.confirm = null;
        applySnapshot(await api.getProject(p.id));
        refreshProjects();
        toast("Clip removed");
      } catch (err) { toast(err.message, true); }
      return;
    }
    if (d.retry && p) {
      try { await api.retryClip(p.id, d.retry); applySnapshot(await api.getProject(p.id)); startWatch(p.id); } catch (err) { toast(err.message, true); }
      return;
    }
    if (d.seek !== undefined) {
      const v = $("#video");
      if (v) { v.currentTime = Number(d.seek); v.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    }
  });

  function addLink() {
    const input = $("#link-input");
    const link = normaliseLink(input?.value);
    if (!link) { toast("That doesn't look like a website address.", true); return; }
    if (state.draft.links.length >= 5) { toast("Up to 5 links per project.", true); return; }
    if (!state.draft.links.includes(link)) state.draft.links.push(link);
    state.draft.linkOpen = false;
    rerenderComposer();
  }

  document.addEventListener("keydown", (e) => {
    const el = e.target;
    if (el.id === "prompt" && e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); return; }
    if (el.id === "link-input" && e.key === "Enter") { e.preventDefault(); addLink(); return; }
    if (el.id?.startsWith("regen-") && e.key === "Enter") { e.preventDefault(); regenerate(el.id.slice(6)); return; }
    const row = el.closest?.("[data-select]");
    if (row && (e.key === "Enter" || e.key === " ") && el === row) { e.preventDefault(); state.selectedClip = row.dataset.select; renderProject(); }
    if (e.key === "Escape") {
      if (state.draft.menu) { state.draft.menu = false; rerenderComposer(); }
      else if (state.confirm) { state.confirm = null; renderProject(); }
    }
  });

  document.addEventListener("input", (e) => {
    const el = e.target;
    if (el.id === "prompt") {
      state.draft.prompt = el.value;
      autoGrow(el);
      const cs = composerState();
      const btn = $('[data-action="send"]');
      if (btn) btn.disabled = !cs.can;
      return;
    }
    if (el.dataset.sf && state.sb) {
      const [i, field] = el.dataset.sf.split(":");
      setSceneField(Number(i), field, el.value);
      return markDirty();
    }
    if (el.id === "bk-name" && state.sb) { state.sb.comp.brand.name = el.value; return markDirty(); }
  });

  document.addEventListener("change", (e) => {
    const el = e.target;
    const d = state.draft;
    if (el.id === "opt-aspect") { d[d.mode].aspect = el.value; return; }
    if (el.id === "opt-target") { d.edit.target = el.value; return; }
    if (el.id === "opt-duration") { d.motion.duration_s = Number(el.value); d.motion.durationTouched = true; return; }
    if (el.id === "auth") { d.authorized = el.checked; return rerenderComposer(); }
    if (el.dataset.imgKind) {
      const im = d.images.find((x) => x.key === el.dataset.imgKind);
      if (im && im.kind !== el.value) { im.kind = el.value; im.status = "uploading"; rerenderComposer(); uploadImage(im); }
      return;
    }
    if (el.id === "r-aspect") { state.ui.renderOpts.aspect = el.value; return; }
    if (el.id === "r-target") { state.ui.renderOpts.target_duration_s = el.value; return; }
    if (el.id === "r-res") { state.ui.renderOpts.resolution = Number(el.value); return; }
    if (el.id === "sb-aspect" && state.sb) { state.sb.comp.meta.aspect = el.value; return markDirty(); }
    if (el.dataset.stpl && state.sb) {
      const sc = state.sb.comp.scenes[Number(el.dataset.stpl)];
      sc.template = el.value;
      if (el.value === "cta_end_card" && !sc.content.cta) sc.content.cta = "Learn more";
      return markDirty(true);
    }
    if (el.dataset.bkColor && state.sb) {
      state.sb.comp.brand.colors = { ...(state.sb.comp.brand.colors || {}), [el.dataset.bkColor]: el.value.toUpperCase() };
      return markDirty();
    }
    if (el.id === "file-input") { addFiles([...el.files], el.dataset.target || null); el.value = ""; return; }
    if (el.id === "image-input") { addFiles([...el.files]); el.value = ""; return; }
    if (el.id === "mobile-project-select") go(el.value === "new" ? { view: "new" } : { view: "project", id: el.value });
  });

  window.addEventListener("hashchange", () => {
    const r = routeFromHash();
    if (r.view !== state.route.view || r.id !== state.route.id) go(r);
  });
  window.addEventListener("beforeunload", (e) => {
    if (state.sb?.dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  // ------------------------------------------------------------------------------------- boot
  (async () => {
    try {
      const [me, catalog] = await Promise.all([api.me(), api.catalog()]);
      state.me = me;
      state.catalog = catalog;
      $("#phase-pill").hidden = !!me.billing_enabled;
      $("#who").textContent = me.billing_enabled ? `${me.plan} plan` : `Up to ${fmtDur(me.limits.max_total_raw_s)} of footage per project · no watermark`;
    } catch (e) {
      toast(`Can't reach the Autocut server: ${e.message}`, true);
    }
    await Promise.all([refreshProjects(), refreshTemplates()]);
    go(routeFromHash());
  })();
})();
