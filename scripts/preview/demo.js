/* Stand-in for the Autocut HTTP API in the hosted preview (no server there).
   Serves one real sample project analyzed by Stage A, and lets visitors walk through the upload flow
   with their own files, which play locally but are not analyzed. Loaded before app.js. */
(() => {
  "use strict";
  const LIMIT = 1800;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const clone = (x) => JSON.parse(JSON.stringify(x));
  let seq = 0;

  class DemoError extends Error {
    constructor(status, code, message, details = {}) { super(message); Object.assign(this, { status, code, details }); }
  }

  function readMeta(file) {
    return new Promise((resolve) => {
      const v = document.createElement("video");
      const url = URL.createObjectURL(file);
      const t = setTimeout(() => resolve({ url, duration: 0, w: 0, h: 0 }), 6000);
      v.preload = "metadata";
      v.onloadedmetadata = () => { clearTimeout(t); resolve({ url, duration: v.duration || 0, w: v.videoWidth, h: v.videoHeight }); };
      v.onerror = () => { clearTimeout(t); resolve({ url, duration: 0, w: 0, h: 0 }); };
      v.src = url;
    });
  }

  class AutocutDemoApi {
    constructor() {
      this.sample = null;
      this.projects = new Map();
      this.uploads = new Map();
      this.ready = fetch("sample/project.json").then((r) => r.json()).then((s) => {
        this.sample = s;
        this.projects.set(s.project.id, { project: s.project, sample: true });
      });
    }

    async me() {
      await this.ready;
      return { id: "usr_preview", plan: "test", billing_enabled: false, projects_this_month: 0,
        limits: { plan: "test", max_total_raw_s: LIMIT, max_clips: 20, projects_per_month: null, watermark: false } };
    }

    async listProjects() {
      await this.ready;
      return [...this.projects.values()].reverse().map(({ project: p }) => ({
        id: p.id, mode: p.mode, title: p.title, status: this.tick(p).status, clip_count: p.clips.length,
        total_raw_s: p.meter.total_raw_s, created_at: p.created_at,
      }));
    }

    /** Advance simulated progress for visitor projects (about 6 s per clip). */
    tick(p) {
      const entry = this.projects.get(p.id);
      if (!entry || entry.sample) return p;
      const now = Date.now();
      p.clips.forEach((c, i) => {
        if (c.status === "ready") return;
        const start = entry.started + i * 2500, f = (now - start) / 6000;
        if (f <= 0) { c.status = "queued"; c.stage = "queued"; c.progress = 0; }
        else if (f < 1) {
          c.status = "processing"; c.progress = Math.min(0.97, f);
          c.stage = f < 0.15 ? "hash" : f < 0.45 ? "proxy" : f < 0.9 ? "signals" : "saving";
        } else { c.status = "ready"; c.stage = null; c.progress = 1; c.proxy_url = c._blob; c.signals_url = "none"; }
      });
      const all = p.clips.every((c) => c.status === "ready");
      p.status = p.clips.length === 0 ? "empty" : all ? "ready" : "analyzing";
      p.progress = p.clips.length ? p.clips.reduce((s, c) => s + c.progress, 0) / p.clips.length : 0;
      p.stage = all ? null : (p.clips.find((c) => c.status === "processing")?.stage || "queued");
      return p;
    }

    meterFor(p) {
      const total = p.clips.reduce((s, c) => s + c.duration_s, 0);
      p.meter = { total_raw_s: total, limit_s: LIMIT, remaining_s: Math.max(0, LIMIT - total), fraction: Math.min(1, total / LIMIT), clip_count: p.clips.length, max_clips: 20 };
    }

    async getProject(id) {
      await this.ready;
      const e = this.projects.get(id);
      if (!e) throw new DemoError(404, "project_not_found", "Project not found");
      return clone(this.tick(e.project));
    }

    async upload(file, onProgress, signal) {
      const meta = await readMeta(file);
      for (let i = 1; i <= 10; i++) {
        if (signal?.aborted) throw new DemoError(0, "aborted", "Upload cancelled");
        await wait(120);
        onProgress(i / 10);
      }
      onProgress(1, "checking");
      await wait(300);
      if (!meta.duration) throw new DemoError(422, "probe_failed", "This file can't be used: the browser could not read it as a video.");
      const up = {
        id: `upl_${++seq}`, filename: file.name, size_bytes: file.size, offset: file.size, status: "complete", chunk_size: 8388608,
        media: { container: file.name.split(".").pop().toLowerCase(), duration_s: meta.duration, display_width: meta.w, display_height: meta.h, width: meta.w, height: meta.h, fps: null, has_audio: null },
        _blob: meta.url,
      };
      this.uploads.set(up.id, up);
      return up;
    }

    clipFromUpload(up, position) {
      return { id: `clp_${++seq}`, upload_id: up.id, filename: up.filename, position, duration_s: up.media.duration_s, media: up.media,
        status: "queued", stage: "queued", progress: 0, error_code: null, error: null, signals_url: null, proxy_url: null, _blob: up._blob };
    }

    async createProject(body) {
      await this.ready;
      const ups = body.upload_ids.map((id) => this.uploads.get(id));
      const total = ups.reduce((s, u) => s + u.media.duration_s, 0);
      if (total > LIMIT) throw new DemoError(402, "total_length_limit_exceeded", "That's over the 30:00 total length for a project. Remove a clip to make room.");
      const p = { id: `prj-${++seq}`, mode: "edit", title: body.title, prompt: body.prompt, options: body.options, status: "analyzing", stage: "queued", progress: 0,
        clips: ups.map((u, i) => this.clipFromUpload(u, i + 1)), created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      this.meterFor(p);
      this.projects.set(p.id, { project: p, started: Date.now() });
      return clone(this.tick(p));
    }

    async addClip(pid, uploadId) {
      const e = this.projects.get(pid);
      if (e.sample) throw new DemoError(409, "read_only", "The sample project can't be changed. Start a new project to try uploads.");
      const c = this.clipFromUpload(this.uploads.get(uploadId), e.project.clips.length + 1);
      e.project.clips.push(c);
      this.meterFor(e.project);
      e.started = Date.now() - e.project.clips.length * 2500 + 2500;
      return clone(c);
    }

    async removeClip(pid, cid) {
      const e = this.projects.get(pid);
      if (e.sample) throw new DemoError(409, "read_only", "The sample project can't be changed. Start a new project to try this.");
      e.project.clips = e.project.clips.filter((c) => c.id !== cid);
      e.project.clips.forEach((c, i) => { c.position = i + 1; });
      this.meterFor(e.project);
    }

    async reorder(pid, ids) {
      const e = this.projects.get(pid);
      const by = Object.fromEntries(e.project.clips.map((c) => [c.id, c]));
      e.project.clips = ids.map((id, i) => Object.assign(by[id], { position: i + 1 }));
      return clone(this.tick(e.project));
    }

    async retryClip() { return null; }

    async deleteProject(pid) {
      if (this.projects.get(pid)?.sample) throw new DemoError(409, "read_only", "The sample project stays in the preview.");
      this.projects.delete(pid);
    }

    async signals(clip) {
      await this.ready;
      const path = this.sample.signals[clip.id];
      if (!path) return null;
      return fetch(path).then((r) => r.json());
    }

    proxyUrl(clip) { return clip.proxy_url; }
    signalsDownloadUrl() { return null; }

    watch(pid, onSnapshot) {
      const id = setInterval(async () => {
        const p = await this.getProject(pid).catch(() => null);
        if (!p) return clearInterval(id);
        onSnapshot(p);
        if (p.status !== "analyzing") clearInterval(id);
      }, 400);
      return () => clearInterval(id);
    }
  }

  window.AutocutDemoApi = AutocutDemoApi;
})();
