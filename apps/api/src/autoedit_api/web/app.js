/* Autocut web app (interim test UI; the full Next.js editor is milestone M9).
   Talks to the same public HTTP API the CLI demo uses. In the hosted preview, window.AutocutDemoApi
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
  const ACCEPTED = [".mp4", ".mov", ".mkv", ".webm"];
  const icon = {
    film: '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="3" width="12" height="10" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M5 3v10M11 3v10M2 6.5h3M11 6.5h3M2 9.5h3M11 9.5h3" stroke="currentColor" stroke-width="1.2"/></svg>',
    spark: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 1.8l1.6 4.1 4.1 1.6-4.1 1.6L8 13.2l-1.6-4.1L2.3 7.5l4.1-1.6L8 1.8z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    up: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 12.5v-9M4 7l4-4 4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    down: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 3.5v9M4 9l4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    x: '<svg viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    plus: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    check: '<svg viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    dot: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="4" fill="currentColor"/></svg>',
    clock: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4"/><path d="M8 4.8V8l2 1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
    info: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.2" stroke="currentColor" stroke-width="1.4"/><path d="M8 7.2v4M8 4.8v.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    retry: '<svg viewBox="0 0 16 16" fill="none"><path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5v3h-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  let toastTimer = 0;
  function toast(msg, isError = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("err", isError);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), isError ? 5200 : 2600);
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
    async req(method, path, body) {
      const res = await fetch(this.base + path, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      if (res.status === 204) return null;
      let data = null;
      try { data = await res.json(); } catch { /* empty body */ }
      if (!res.ok) {
        const e = data?.error || {};
        throw new ApiError(res.status, e.code || "error", e.message || `Request failed (${res.status})`, e.details);
      }
      return data;
    }
    me() { return this.req("GET", "/v1/me"); }
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
      const poll = async () => {
        if (closed) return;
        try {
          const p = await this.getProject(pid);
          onSnapshot(p);
          if (p.status === "analyzing") timer = setTimeout(poll, 1500);
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
      "Cut this into a punchy 45-second Reel with word-by-word captions",
      "Tighten my talking-head video: remove pauses and filler words",
      "Make a calm 2-minute tutorial for YouTube, keep every step",
    ],
    motion: [
      "30-second promo for my bakery from bakery.com, warm and friendly",
      "Launch video for our budgeting app, 9:16, ends with 'Download free'",
    ],
  };
  const PLACEHOLDER = {
    edit: "Describe the edit you want: platform, length, tone, anything to keep or remove…",
    motion: "Tell us about the business: what it offers, who it's for, the goal, tone and call to action…",
  };

  const state = {
    me: null,
    projects: [],
    route: { view: "new" },
    project: null,
    selectedClip: null,
    signals: new Map(),
    stopWatch: null,
    confirm: null, // key of an inline confirmation in progress
    draft: {
      mode: "edit",
      prompt: "",
      clips: [], // {key, file, name, size, duration, status, progress, upload, error, ctrl}
      options: { goal: "short_vertical", target_duration_s: "", style: "energetic", captions: true, broll: true, sfx: true, color_grade: true, music: true, allow_reorder: false },
    },
  };
  const limitS = () => state.me?.limits?.max_total_raw_s ?? 1800;
  const maxClips = () => state.me?.limits?.max_clips ?? 20;

  // ------------------------------------------------------------------------------------ routing
  function go(route) {
    state.route = route;
    state.confirm = null;
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

  async function openProject(id) {
    state.project = null;
    state.selectedClip = null;
    render();
    try {
      const p = await api.getProject(id);
      applySnapshot(p);
      if (p.status === "analyzing") state.stopWatch = api.watch(id, applySnapshot);
    } catch (e) {
      toast(e.code === "project_not_found" ? "That project no longer exists." : e.message, true);
      go({ view: "new" });
    }
  }

  function applySnapshot(p) {
    if (state.route.view !== "project" || state.route.id !== p.id) return;
    const prevStatus = state.project?.status;
    state.project = p;
    if (!state.selectedClip || !p.clips.some((c) => c.id === state.selectedClip)) {
      state.selectedClip = (p.clips.find((c) => c.status === "ready") || p.clips[0])?.id || null;
    }
    const sel = p.clips.find((c) => c.id === state.selectedClip);
    if (sel && sel.status !== "ready") {
      const ready = p.clips.find((c) => c.status === "ready");
      if (ready) state.selectedClip = ready.id;
    }
    const item = state.projects.find((x) => x.id === p.id);
    if (item) { item.status = p.status; item.clip_count = p.clips.length; item.total_raw_s = p.meter.total_raw_s; }
    renderSidebar();
    renderProject();
    if (prevStatus === "analyzing" && p.status !== "analyzing") {
      toast(p.status === "ready" ? "Analysis finished" : "Analysis finished with problems", p.status !== "ready");
    }
  }

  // ------------------------------------------------------------------------------------ sidebar
  function renderSidebar() {
    const list = $("#project-list");
    if (!state.projects.length) {
      list.innerHTML = `<div class="muted" style="padding:8px 10px;font-size:13px">No projects yet.</div>`;
    } else {
      list.innerHTML = state.projects.map((p) => `
        <button class="project-item" type="button" data-open="${esc(p.id)}" aria-current="${state.route.view === "project" && state.route.id === p.id}">
          <span class="name">${esc(p.title || "Untitled project")}</span>
          <span class="status-dot ${esc(p.status)}" title="${esc(p.status)}"></span>
          <span class="meta">${p.clip_count} clip${p.clip_count === 1 ? "" : "s"} · ${fmtDur(p.total_raw_s)}</span>
        </button>`).join("");
    }
    const sel = $("#mobile-project-select");
    sel.innerHTML = `<option value="new">+ New project</option>` + state.projects
      .map((p) => `<option value="${esc(p.id)}" ${state.route.id === p.id ? "selected" : ""}>${esc(p.title || "Untitled project")}</option>`).join("");
    if (state.route.view === "new") sel.value = "new";
  }

  // ------------------------------------------------------------------------------------ render
  function render() {
    renderSidebar();
    if (state.route.view === "project") renderProject();
    else renderComposer();
  }

  // ---- start screen ---------------------------------------------------------------------------
  function draftTotal() { return state.draft.clips.reduce((s, c) => s + (c.duration || 0), 0); }

  function meterHtml(total, limit, count) {
    const over = total > limit + 0.01;
    return `
      <div class="meter ${over ? "over" : ""}">
        <div class="meter-row">
          <span><b class="mono" style="color:var(--fg)">${fmtDur(total)}</b> of ${fmtDur(limit)} total raw length</span>
          <span>${count} / ${maxClips()} clips</span>
        </div>
        <div class="meter-track"><div class="meter-fill" style="width:${Math.min(100, (total / limit) * 100).toFixed(1)}%"></div></div>
      </div>`;
  }

  function renderComposer() {
    const d = state.draft;
    const editing = d.mode === "edit";
    const uploading = d.clips.some((c) => c.status === "uploading" || c.status === "checking");
    const ready = d.clips.filter((c) => c.status === "ready");
    const errors = d.clips.filter((c) => c.status === "error");
    const total = draftTotal();
    const canSend = editing && ready.length > 0 && !uploading && !errors.length && total <= limitS() + 0.01;
    let note;
    if (!editing) note = "Motion graphics is coming soon. Your prompt is kept if you switch back.";
    else if (!d.clips.length) note = "Add at least one video to start.";
    else if (uploading) note = "Uploading… you can keep writing your prompt.";
    else if (errors.length) note = "Remove the clips marked in red to continue.";
    else note = `${ready.length} clip${ready.length === 1 ? "" : "s"} ready. Autocut analyzes every frame and the audio.`;

    $("#view").innerHTML = `
      <div class="wrap">
        <div class="hero">
          <span class="eyebrow">New project</span>
          <h1>${editing ? "Drop your raw footage. Get a finished edit." : "Turn your business into motion graphics."}</h1>
          <p>${editing
            ? "Upload one or more clips, tell Autocut what you want, and it finds the story, cuts the dead air, and shows you every decision before anything renders."
            : "Add your website, app listing, product photos or brand files and describe the video you need."}</p>
        </div>
        <section class="composer" aria-label="Start a project">
          <div class="modes" role="group" aria-label="Mode">
            <button type="button" class="mode" data-mode="edit" aria-pressed="${editing}">${icon.film} Edit video</button>
            <button type="button" class="mode" data-mode="motion" aria-pressed="${!editing}">${icon.spark} Make motion graphics <span class="soon">Soon</span></button>
          </div>
          ${editing ? `
          <div class="drop" id="drop">
            <div class="drop-cta">
              <button type="button" class="btn" data-action="pick">${icon.plus} Add videos</button>
              <span class="hint">or drag them here · MP4, MOV, MKV, WebM · uploads resume if your connection drops</span>
            </div>
            ${d.clips.length ? `<div class="clips">${d.clips.map((c, i) => clipChip(c, i)).join("")}</div>` : ""}
            ${meterHtml(total, limitS(), d.clips.length)}
          </div>` : `
          <div class="notice">Motion graphics mode is being built (website and brand intake, templates, voiceover). ${d.clips.length ? `Your ${d.clips.length} video${d.clips.length === 1 ? " is" : "s are"} kept for Edit video.` : ""}</div>`}
          <div class="prompt">
            <label class="eyebrow" for="prompt" style="display:block;padding:6px 6px 0">What should the video be?</label>
            <textarea id="prompt" placeholder="${esc(PLACEHOLDER[d.mode])}">${esc(d.prompt)}</textarea>
          </div>
          <div class="examples">${EXAMPLES[d.mode].map((t) => `<button type="button" class="example" data-example="${esc(t)}">${esc(t)}</button>`).join("")}</div>
          ${editing ? `
          <div class="options">
            <label class="field" for="opt-goal">Format
              <select id="opt-goal">
                <option value="short_vertical" ${d.options.goal === "short_vertical" ? "selected" : ""}>Short-form vertical</option>
                <option value="youtube" ${d.options.goal === "youtube" ? "selected" : ""}>YouTube</option>
                <option value="promo" ${d.options.goal === "promo" ? "selected" : ""}>Promo</option>
              </select></label>
            <label class="field" for="opt-style">Style
              <select id="opt-style">
                ${[["energetic", "Energetic Short"], ["clean", "Clean YouTube"], ["cinematic", "Cinematic"], ["product", "Product Promo"], ["calm", "Calm Tutorial"]]
                  .map(([v, l]) => `<option value="${v}" ${d.options.style === v ? "selected" : ""}>${l}</option>`).join("")}
              </select></label>
            <label class="field" for="opt-target">Target length
              <input id="opt-target" type="number" min="5" max="3600" step="5" placeholder="auto" value="${esc(d.options.target_duration_s)}"> s</label>
            ${[["captions", "Captions"], ["broll", "B-roll"], ["sfx", "Sound effects"], ["color_grade", "Color grade"], ["music", "Music"], ["allow_reorder", "Let AI reorder"]]
              .map(([k, l]) => `<label class="toggle" for="opt-${k}"><input type="checkbox" id="opt-${k}" data-opt="${k}" ${d.options[k] ? "checked" : ""}> ${l}</label>`).join("")}
          </div>` : ""}
          <div class="composer-foot">
            <span class="note">${esc(note)}</span>
            <button type="button" class="btn primary" data-action="send" ${canSend ? "" : "disabled"}>Start editing</button>
          </div>
        </section>
        ${DEMO ? `<div class="banner" style="max-width:760px;margin:0 auto;width:100%">${icon.info}<span>This is a hosted preview with sample footage. Uploads are simulated here; run Autocut on your machine to analyze your own videos. Open <b>Sample project</b> in the project list to see a finished analysis of three real clips.</span></div>` : ""}
      </div>`;
    bindDrop();
  }

  function clipChip(c, i) {
    const err = c.status === "error";
    let sub;
    if (c.status === "uploading") sub = `Uploading ${pct(c.progress)} · ${fmtBytes(c.size)}`;
    else if (c.status === "checking") sub = "Checking the file…";
    else if (err) sub = `<span style="color:var(--bad)">${esc(c.error)}</span>`;
    else {
      const m = c.upload?.media;
      sub = m ? [fmtDur(m.duration_s), `${m.display_width}×${m.display_height}`, m.fps ? `${Math.round(m.fps)} fps` : "", m.has_audio == null ? "" : m.has_audio ? "audio" : "no audio"].filter(Boolean).join(" · ") : fmtDur(c.duration);
    }
    return `
      <div class="clip-chip ${err ? "err" : ""}">
        <span class="idx">${i + 1}</span>
        <div style="min-width:0">
          <div class="fname" title="${esc(c.name)}">${esc(c.name)}</div>
          <div class="sub">${sub}</div>
          ${c.status === "uploading" || c.status === "checking" ? `<div class="bar"><i style="width:${(c.progress * 100).toFixed(1)}%"></i></div>` : ""}
        </div>
        <div class="actions">
          <button type="button" class="btn icon ghost" data-move="${c.key}:-1" aria-label="Move up" ${i === 0 ? "disabled" : ""}>${icon.up}</button>
          <button type="button" class="btn icon ghost" data-move="${c.key}:1" aria-label="Move down" ${i === state.draft.clips.length - 1 ? "disabled" : ""}>${icon.down}</button>
        </div>
        <button type="button" class="btn icon ghost" data-remove-draft="${c.key}" aria-label="Remove ${esc(c.name)}">${icon.x}</button>
      </div>`;
  }

  function bindDrop() {
    const drop = $("#drop");
    if (!drop) return;
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      addFiles([...(e.dataTransfer?.files || [])]);
    });
  }

  /** Read a video's duration in the browser so an over-limit clip is refused before it uploads. */
  function localDuration(file) {
    return new Promise((resolve) => {
      const v = document.createElement("video");
      const url = URL.createObjectURL(file);
      const done = (d) => { URL.revokeObjectURL(url); resolve(d); };
      const t = setTimeout(() => done(null), 5000);
      v.preload = "metadata";
      v.onloadedmetadata = () => { clearTimeout(t); done(Number.isFinite(v.duration) ? v.duration : null); };
      v.onerror = () => { clearTimeout(t); done(null); };
      v.src = url;
    });
  }

  let keySeq = 0;
  async function addFiles(files, intoProject = null) {
    for (const file of files) {
      const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
      if (!ACCEPTED.includes(ext)) { toast(`${file.name} isn't a supported video (MP4, MOV, MKV or WebM).`, true); continue; }
      if (intoProject) { uploadIntoProject(file, intoProject); continue; }
      if (state.draft.clips.length >= maxClips()) { toast(`A project holds at most ${maxClips()} clips.`, true); break; }
      const clip = { key: `k${++keySeq}`, file, name: file.name, size: file.size, duration: 0, status: "uploading", progress: 0 };
      const est = await localDuration(file);
      if (est && draftTotal() + est > limitS() + 0.01) {
        toast(`${file.name} (${fmtDur(est)}) would take this project past the ${fmtDur(limitS())} limit. Remove a clip to make room.`, true);
        continue;
      }
      clip.duration = est || 0;
      state.draft.clips.push(clip);
      renderComposerIfOpen();
      startUpload(clip);
    }
  }

  function renderComposerIfOpen() {
    if (state.route.view !== "new") return;
    const ta = $("#prompt");
    const focused = document.activeElement === ta;
    const pos = ta ? [ta.selectionStart, ta.selectionEnd] : null;
    renderComposer();
    if (focused) { const n = $("#prompt"); n.focus(); n.setSelectionRange(pos[0], pos[1]); }
  }

  async function startUpload(clip) {
    clip.ctrl = new AbortController();
    let last = 0;
    try {
      const up = await api.upload(clip.file, (p, phase) => {
        clip.progress = p;
        if (phase === "checking") clip.status = "checking";
        const now = performance.now();
        if (now - last > 120 || p >= 1) { last = now; renderComposerIfOpen(); }
      }, clip.ctrl.signal);
      clip.upload = up;
      clip.duration = up.media.duration_s;
      clip.status = draftTotal() > limitS() + 0.01 ? "error" : "ready";
      if (clip.status === "error") clip.error = `Over the ${fmtDur(limitS())} total limit. Remove a clip.`;
    } catch (e) {
      if (e.code === "aborted" || e.name === "AbortError") return;
      clip.status = "error";
      clip.error = e.message;
    }
    renderComposerIfOpen();
  }

  async function uploadIntoProject(file, pid) {
    toast(`Uploading ${file.name}…`);
    try {
      const up = await api.upload(file, () => {});
      await api.addClip(pid, up.id);
      toast(`${file.name} added`);
      if (state.route.view === "project" && state.route.id === pid) {
        const p = await api.getProject(pid);
        applySnapshot(p);
        if (state.stopWatch) state.stopWatch();
        state.stopWatch = api.watch(pid, applySnapshot);
      }
      refreshProjects();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function send() {
    const d = state.draft;
    const ready = d.clips.filter((c) => c.status === "ready");
    const title = (d.prompt.trim().split(/\s+/).slice(0, 7).join(" ") || ready[0]?.name.replace(/\.[^.]+$/, "") || "Untitled project").slice(0, 120);
    const options = { ...d.options, target_duration_s: d.options.target_duration_s ? Number(d.options.target_duration_s) : null };
    const btn = $('[data-action="send"]');
    if (btn) { btn.disabled = true; btn.textContent = "Starting…"; }
    try {
      const p = await api.createProject({ mode: "edit", title, prompt: d.prompt.trim(), options, upload_ids: ready.map((c) => c.upload.id) });
      state.draft.clips = [];
      state.draft.prompt = "";
      await refreshProjects();
      go({ view: "project", id: p.id });
    } catch (e) {
      toast(e.message, true);
      renderComposer();
    }
  }

  // ---- project view ---------------------------------------------------------------------------
  const STAGES = [
    { key: "analyze", label: "Analyzing", desc: "Frames, motion, faces, audio, scenes" },
    { key: "understand", label: "Understanding", desc: "Transcript and story", later: "Next update" },
    { key: "cut", label: "Cutting", desc: "Edit decision list", later: "Coming soon" },
    { key: "enhance", label: "Enhancing", desc: "Captions, effects, color, sound", later: "Coming soon" },
    { key: "render", label: "Rendering", desc: "Final video and export", later: "Coming soon" },
  ];

  const STAGE_LABEL = {
    hash: "reading file", probe: "reading file", starting: "starting", downloading: "fetching file",
    proxy: "making preview", signals: "analyzing frames and audio", saving: "saving", retrying: "retrying", queued: "waiting",
  };
  const stageLabel = (s) => STAGE_LABEL[s] || s || "waiting";

  function statusPill(status) {
    const map = { ready: ["ok", "Analyzed"], analyzing: ["accent", "Analyzing"], failed: ["bad", "Failed"], partial: ["warn", "Needs attention"], empty: ["", "No clips"] };
    const [cls, label] = map[status] || ["", status];
    return `<span class="pill ${cls}"><span class="dot"></span>${label}</span>`;
  }

  function renderProject() {
    if (state.route.view !== "project") return;
    const view = $("#view");
    const p = state.project;
    if (!p) { view.innerHTML = `<div class="wrap"><div class="empty">Loading project…</div></div>`; return; }
    const analyzing = p.status === "analyzing";
    const confirmDel = state.confirm === "delete-project";
    const stageState = (s) => (s.key === "analyze" ? (analyzing ? "active" : p.status === "empty" ? "later" : "done") : "later");
    const stageNote = (s) => (s.key === "analyze" ? (analyzing ? `${pct(p.progress)} · ${esc(stageLabel(p.stage))}` : p.status === "empty" ? "Add a clip" : "Complete") : s.later);
    const sel = p.clips.find((c) => c.id === state.selectedClip);

    view.innerHTML = `
      <div class="wrap">
        <div class="proj-head">
          <div class="titles">
            <span class="eyebrow">Edit video project</span>
            <h1>${esc(p.title || "Untitled project")}</h1>
            ${p.prompt ? `<div class="prompt-quote">“${esc(p.prompt)}”</div>` : ""}
          </div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            ${statusPill(p.status)}
            <button type="button" class="btn small" data-action="add-to-project">${icon.plus} Add clip</button>
            ${confirmDel
              ? `<span class="confirm">Delete project and all its data? <button type="button" class="btn small danger" data-action="delete-project-yes">Delete</button><button type="button" class="btn small ghost" data-action="cancel-confirm">Keep</button></span>`
              : `<button type="button" class="btn small ghost danger" data-action="delete-project">Delete</button>`}
          </div>
        </div>
        ${meterHtml(p.meter.total_raw_s, p.meter.limit_s, p.meter.clip_count)}
        <section class="card" aria-label="Pipeline">
          <div class="stages">
            ${STAGES.map((s) => `
              <div class="stage ${stageState(s)}">
                <span class="label">${stageState(s) === "done" ? icon.check : stageState(s) === "active" ? icon.dot : icon.clock}${s.label}</span>
                <span class="desc">${s.desc}</span>
                <span class="desc mono">${stageNote(s)}</span>
              </div>`).join("")}
          </div>
        </section>
        <section class="card" aria-label="Clips">
          <div class="card-head"><h2>Clips</h2><span class="muted" style="font-size:12px">In project order. Select a clip to see what Autocut found.</span></div>
          <div class="clip-rows">${p.clips.length ? p.clips.map((c, i) => clipRow(c, i, p.clips.length)).join("") : `<div class="empty">This project has no clips. <button type="button" class="btn primary small" data-action="add-to-project">${icon.plus} Add clip</button></div>`}</div>
        </section>
        <div id="analysis">${sel && sel.status === "ready" ? `<div class="empty">Loading analysis…</div>` : ""}</div>
      </div>`;
    if (sel && sel.status === "ready") loadAnalysis(sel);
  }

  function clipRow(c, i, n) {
    const m = c.media;
    const confirm = state.confirm === `remove-${c.id}`;
    let status;
    if (c.status === "ready") status = `<span class="pill ok"><span class="dot"></span>Ready</span>`;
    else if (c.status === "failed") status = `<span class="pill bad"><span class="dot"></span>Failed</span>`;
    else status = `<span class="pill accent"><span class="dot"></span>${c.status === "queued" ? (c.stage === "retrying" ? "Retrying" : "Queued") : "Analyzing"}</span>`;
    const thumb = c.status === "ready" && api.proxyUrl(c)
      ? `<video src="${esc(api.proxyUrl(c))}#t=${(c.duration_s * 0.4).toFixed(1)}" muted preload="metadata" playsinline></video>`
      : "";
    return `
      <div class="clip-row" role="button" tabindex="0" data-select="${esc(c.id)}" aria-current="${state.selectedClip === c.id}">
        <span class="mono muted" style="text-align:center">${i + 1}</span>
        <div class="thumb">${thumb}<span class="dur">${fmtDur(c.duration_s)}</span></div>
        <div class="info">
          <span class="fname" title="${esc(c.filename)}">${esc(c.filename)}</span>
          <span class="facts">
            ${status}
            ${m ? `<span>${m.display_width}×${m.display_height}</span>${m.fps ? `<span>${Math.round(m.fps)} fps</span>` : ""}${m.has_audio == null ? "" : `<span>${m.has_audio ? "audio" : "no audio"}</span>`}` : ""}
            ${c.status === "processing" || c.status === "queued" ? `<span class="mono">${pct(c.progress)} · ${esc(stageLabel(c.stage))}</span>` : ""}
            ${c.status === "failed" ? `<span style="color:var(--bad)">${esc(c.error || c.error_code)}</span>` : ""}
          </span>
          ${c.status === "processing" || c.status === "queued" ? `<div class="progress"><i style="width:${(c.progress * 100).toFixed(1)}%"></i></div>` : ""}
        </div>
        <div class="row-actions">
          ${c.status === "failed" ? `<button type="button" class="btn icon ghost" data-retry="${esc(c.id)}" aria-label="Retry">${icon.retry}</button>` : ""}
          <button type="button" class="btn icon ghost" data-pmove="${esc(c.id)}:-1" aria-label="Move up" ${i === 0 ? "disabled" : ""}>${icon.up}</button>
          <button type="button" class="btn icon ghost" data-pmove="${esc(c.id)}:1" aria-label="Move down" ${i === n - 1 ? "disabled" : ""}>${icon.down}</button>
          ${confirm
            ? `<button type="button" class="btn small danger" data-premove-yes="${esc(c.id)}">Remove</button>`
            : `<button type="button" class="btn icon ghost" data-premove="${esc(c.id)}" aria-label="Remove clip">${icon.x}</button>`}
        </div>
      </div>`;
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

  // ------------------------------------------------------------------------------------ events
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-select], a");
    if (!t) return;
    const d = t.dataset;
    if (t.id === "new-project") return go({ view: "new" });
    if (d.open) return go({ view: "project", id: d.open });
    if (d.mode) { state.draft.mode = d.mode; return renderComposer(); }
    if (d.example) { state.draft.prompt = d.example; renderComposer(); $("#prompt").focus(); return; }
    if (d.action === "pick") { const fi = $("#file-input"); fi.dataset.target = ""; fi.click(); return; }
    if (d.action === "add-to-project") { const fi = $("#file-input"); fi.dataset.target = state.project.id; fi.click(); return; }
    if (d.action === "send") return send();
    if (d.removeDraft) {
      const c = state.draft.clips.find((x) => x.key === d.removeDraft);
      c?.ctrl?.abort();
      state.draft.clips = state.draft.clips.filter((x) => x.key !== d.removeDraft);
      // Clips that were only over the limit become valid again once there is room.
      for (const x of state.draft.clips) {
        if (x.status === "error" && x.upload && draftTotal() <= limitS() + 0.01) { x.status = "ready"; x.error = null; }
      }
      return renderComposer();
    }
    if (d.move) {
      const [key, dir] = d.move.split(":");
      const arr = state.draft.clips, i = arr.findIndex((x) => x.key === key), j = i + Number(dir);
      if (i >= 0 && j >= 0 && j < arr.length) [arr[i], arr[j]] = [arr[j], arr[i]];
      return renderComposer();
    }
    const p = state.project;
    if (d.select && !t.matches("button")) {
      if (e.target.closest("button")) return;
      state.selectedClip = d.select;
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
      try {
        await api.retryClip(p.id, d.retry);
        applySnapshot(await api.getProject(p.id));
        if (state.stopWatch) state.stopWatch();
        state.stopWatch = api.watch(p.id, applySnapshot);
      } catch (err) { toast(err.message, true); }
      return;
    }
    if (d.action === "delete-project") { state.confirm = "delete-project"; return renderProject(); }
    if (d.action === "cancel-confirm") { state.confirm = null; return renderProject(); }
    if (d.action === "delete-project-yes" && p) {
      try {
        await api.deleteProject(p.id);
        toast("Project deleted");
        await refreshProjects();
        go({ view: "new" });
      } catch (err) { toast(err.message, true); }
      return;
    }
    if (d.seek !== undefined) {
      const v = $("#video");
      if (v) { v.currentTime = Number(d.seek); v.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    }
  });

  document.addEventListener("keydown", (e) => {
    const row = e.target.closest?.("[data-select]");
    if (row && (e.key === "Enter" || e.key === " ") && e.target === row) { e.preventDefault(); state.selectedClip = row.dataset.select; renderProject(); }
    if (e.key === "Escape" && state.confirm) { state.confirm = null; renderProject(); }
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "prompt") state.draft.prompt = e.target.value;
    if (e.target.id === "opt-target") state.draft.options.target_duration_s = e.target.value;
  });
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.id === "opt-goal") state.draft.options.goal = el.value;
    if (el.id === "opt-style") state.draft.options.style = el.value;
    if (el.dataset.opt) state.draft.options[el.dataset.opt] = el.checked;
    if (el.id === "file-input") {
      const target = el.dataset.target || null;
      addFiles([...el.files], target);
      el.value = "";
    }
    if (el.id === "mobile-project-select") go(el.value === "new" ? { view: "new" } : { view: "project", id: el.value });
  });
  window.addEventListener("hashchange", () => {
    const r = routeFromHash();
    if (r.view !== state.route.view || r.id !== state.route.id) go(r);
  });

  // ------------------------------------------------------------------------------------- boot
  (async () => {
    try {
      state.me = await api.me();
      $("#phase-pill").hidden = !!state.me.billing_enabled;
      $("#who").textContent = state.me.billing_enabled
        ? `${state.me.plan} plan`
        : `Up to ${fmtDur(state.me.limits.max_total_raw_s)} of footage per project · no watermark`;
    } catch (e) {
      toast(`Can't reach the Autocut server: ${e.message}`, true);
    }
    await refreshProjects();
    go(routeFromHash());
  })();
})();
