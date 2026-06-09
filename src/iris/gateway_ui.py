"""Embedded HTML for the Iris gateway dashboard.

Served at /dashboard (and /memory-browser for backwards compatibility).
Single-file vanilla JS app; talks to the gateway JSON API with the
bearer token the user provides in the sidebar.
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Iris Console</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='13' fill='none' stroke='%238da2ff' stroke-width='2.5'/><circle cx='16' cy='16' r='5' fill='%238da2ff'/></svg>">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Spline+Sans:wght@400;500;600&family=Spline+Sans+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090b10;
      --panel-bg: linear-gradient(180deg, rgba(148,163,210,.055), rgba(148,163,210,.02));
      --raised: #0e111a;
      --line: rgba(148,163,210,.12);
      --line-soft: rgba(148,163,210,.07);
      --text: #e7eaf4;
      --muted: #8a92aa;
      --faint: #5b6377;
      --iris: #8da2ff;
      --iris-strong: #aab9ff;
      --iris-deep: #5d74e6;
      --moss: #5dc794;
      --amber: #e3b562;
      --rust: #e0705f;
      --display: "Instrument Serif", Georgia, "Iowan Old Style", serif;
      --sans: "Spline Sans", "Avenir Next", "Segoe UI", sans-serif;
      --mono: "Spline Sans Mono", ui-monospace, "SF Mono", Menlo, monospace;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; }
    body {
      min-height: 100vh;
      color: var(--text);
      font: 400 15px/1.5 var(--sans);
      background:
        radial-gradient(900px 480px at 72% -12%, rgba(141,162,255,.13), transparent 70%),
        radial-gradient(720px 460px at 8% 112%, rgba(93,199,148,.06), transparent 70%),
        var(--bg);
      background-attachment: fixed;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: radial-gradient(rgba(148,163,210,.055) 1px, transparent 1.4px);
      background-size: 26px 26px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,.8), rgba(0,0,0,.25));
    }
    ::selection { background: rgba(141,162,255,.35); }
    :focus-visible { outline: 2px solid var(--iris); outline-offset: 2px; border-radius: 4px; }

    .app { display: grid; grid-template-columns: 238px minmax(0, 1fr); min-height: 100vh; position: relative; }

    /* ---------- sidebar ---------- */
    .side {
      border-right: 1px solid var(--line-soft);
      background: rgba(9,11,16,.72);
      backdrop-filter: blur(10px);
      padding: 24px 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 28px;
      position: sticky;
      top: 0;
      height: 100vh;
    }
    .brand { display: flex; align-items: center; gap: 12px; padding: 0 8px; }
    .aperture {
      width: 38px; height: 38px; border-radius: 50%; flex: none;
      border: 1px solid rgba(141,162,255,.4);
      background:
        radial-gradient(circle at 50% 50%, var(--iris-strong) 0 17%, rgba(141,162,255,.55) 18% 26%, transparent 27%),
        conic-gradient(from 30deg, rgba(141,162,255,.5), rgba(141,162,255,.05) 25%, rgba(141,162,255,.45) 50%, rgba(141,162,255,.05) 75%, rgba(141,162,255,.5));
      box-shadow: 0 0 24px rgba(141,162,255,.28), inset 0 0 10px rgba(9,11,16,.8);
    }
    .word { font-family: var(--display); font-style: italic; font-size: 1.6rem; line-height: 1; }
    .brand-sub { font: 500 .62rem var(--mono); letter-spacing: .34em; text-transform: uppercase; color: var(--faint); margin-top: 4px; }

    nav { display: flex; flex-direction: column; gap: 2px; }
    .nav-item {
      display: flex; align-items: center; gap: 11px;
      padding: 9px 11px; border: 0; border-radius: 10px;
      background: none; color: var(--muted);
      font: 500 .9rem var(--sans); text-align: left; cursor: pointer;
      position: relative; transition: color .15s ease, background .15s ease;
    }
    .nav-item:hover { color: var(--text); background: rgba(148,163,210,.07); }
    .nav-item.active { color: var(--text); background: rgba(141,162,255,.11); }
    .nav-item.active::before {
      content: ""; position: absolute; left: -16px; top: 8px; bottom: 8px;
      width: 2px; border-radius: 2px; background: var(--iris);
    }
    .nav-item svg { width: 15px; height: 15px; stroke: currentColor; fill: none; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; flex: none; }
    .nav-badge {
      margin-left: auto; font: 600 .68rem var(--mono);
      color: var(--amber); background: rgba(227,181,98,.14);
      border-radius: 999px; padding: 1px 7px;
    }
    .nav-badge:empty { display: none; }

    .side-foot { margin-top: auto; display: grid; gap: 10px; padding: 0 4px; }
    .conn { display: flex; align-items: center; gap: 8px; font: 500 .74rem var(--mono); color: var(--muted); }
    .field-label { font: 500 .64rem var(--mono); letter-spacing: .18em; text-transform: uppercase; color: var(--faint); }

    input, select {
      width: 100%;
      background: #0c0f16;
      border: 1px solid var(--line);
      border-radius: 10px;
      color: var(--text);
      font: 400 .88rem var(--sans);
      padding: 9px 11px;
      outline: none;
      transition: border-color .15s ease, box-shadow .15s ease;
    }
    input:focus, select:focus { border-color: var(--iris-deep); box-shadow: 0 0 0 3px rgba(141,162,255,.14); }
    input::placeholder { color: var(--faint); }

    /* ---------- main ---------- */
    main { padding: 30px clamp(20px, 4vw, 48px) 64px; max-width: 1240px; width: 100%; }
    .top { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 24px; }
    .top h1 { font: 400 2.3rem/1.05 var(--display); margin: 0; }
    .top p { margin: 7px 0 0; color: var(--muted); font-size: .92rem; max-width: 580px; }
    .top-actions { display: flex; gap: 8px; flex: none; }

    button { font: inherit; cursor: pointer; border-radius: 10px; border: 1px solid transparent; transition: background .15s ease, border-color .15s ease, color .15s ease, transform .15s ease; }
    .primary { background: var(--iris); color: #0a0c11; font-weight: 600; padding: 9px 16px; }
    .primary:hover { background: var(--iris-strong); transform: translateY(-1px); }
    .ghost { background: transparent; border-color: var(--line); color: var(--text); padding: 8px 14px; font-size: .86rem; }
    .ghost:hover { border-color: rgba(141,162,255,.5); color: var(--iris-strong); }
    .btn-good { background: rgba(93,199,148,.1); border-color: rgba(93,199,148,.32); color: var(--moss); padding: 8px 14px; font-size: .86rem; }
    .btn-good:hover { background: rgba(93,199,148,.2); }
    .btn-danger { background: rgba(224,112,95,.09); border-color: rgba(224,112,95,.3); color: var(--rust); padding: 8px 14px; font-size: .86rem; }
    .btn-danger:hover { background: rgba(224,112,95,.18); }
    .small { padding: 5px 11px; font-size: .78rem; border-radius: 8px; }

    /* ---------- stats ---------- */
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 12px; margin-bottom: 14px; }
    .stat {
      background: var(--panel-bg);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px 18px 14px;
      display: grid; gap: 7px;
      text-align: left; color: var(--text); cursor: pointer;
    }
    .stat:hover { border-color: rgba(141,162,255,.45); transform: translateY(-2px); }
    .stat-label { font: 500 .66rem var(--mono); letter-spacing: .18em; text-transform: uppercase; color: var(--muted); }
    .stat-value { font: 400 2.7rem/1 var(--display); }
    .stat-value.k-run { color: var(--iris-strong); }
    .stat-value.k-wait { color: var(--amber); }
    .stat-value.k-good { color: var(--moss); }
    .stat-hint { font-size: .74rem; color: var(--faint); }
    .stat:hover .stat-hint { color: var(--iris-strong); }

    /* ---------- panels, rows, cards ---------- */
    .panel { background: var(--panel-bg); border: 1px solid var(--line); border-radius: 16px; padding: 16px 18px; min-width: 0; }
    .panel-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 8px; }
    .panel-head h2 { font: 400 1.3rem var(--display); margin: 0; }
    .cols { display: grid; grid-template-columns: 1.55fr 1fr; gap: 12px; align-items: start; }

    .row { display: flex; align-items: center; gap: 12px; padding: 11px 2px; border-top: 1px solid var(--line-soft); }
    .row:first-child { border-top: 0; }
    .row-main { min-width: 0; display: grid; gap: 2px; }
    .row-title { font-weight: 500; font-size: .9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row-meta { font: 400 .72rem var(--mono); color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row-side { margin-left: auto; display: flex; align-items: center; gap: 10px; flex: none; }
    .row-time { font: 400 .76rem var(--mono); color: var(--muted); }

    .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--faint); }
    .dot.k-run { background: var(--iris); animation: pulse 1.8s ease-in-out infinite; }
    .dot.k-good { background: var(--moss); }
    .dot.k-bad { background: var(--rust); }
    .dot.k-wait { background: var(--amber); }
    @keyframes pulse {
      0%, 100% { box-shadow: 0 0 0 0 rgba(141,162,255,.45); }
      50% { box-shadow: 0 0 0 5px rgba(141,162,255,0); }
    }

    .chips { display: flex; gap: 6px; flex-wrap: wrap; }
    .chip { background: transparent; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); padding: 5px 13px; font-size: .8rem; }
    .chip:hover { color: var(--text); border-color: rgba(141,162,255,.4); }
    .chip.active { background: rgba(141,162,255,.13); border-color: rgba(141,162,255,.45); color: var(--iris-strong); }

    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 12px; }
    .card { background: var(--panel-bg); border: 1px solid var(--line); border-radius: 16px; padding: 16px; display: grid; gap: 10px; align-content: start; animation: rise .4s ease both; }
    .card:hover { border-color: rgba(141,162,255,.3); }
    @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }

    .fact-line { font-size: 1rem; line-height: 1.45; overflow-wrap: anywhere; }
    .fact-line .pred { font-family: var(--display); font-style: italic; color: var(--iris-strong); padding: 0 2px; }
    .conf { height: 3px; border-radius: 2px; background: rgba(148,163,210,.14); overflow: hidden; }
    .conf i { display: block; height: 100%; background: linear-gradient(90deg, var(--iris-deep), var(--iris-strong)); border-radius: 2px; }

    .pills { display: flex; flex-wrap: wrap; gap: 6px; }
    .pill { font: 500 .68rem var(--mono); border: 1px solid var(--line); border-radius: 999px; padding: 2px 9px; color: var(--muted); }
    .pill.on { color: var(--amber); border-color: rgba(227,181,98,.4); background: rgba(227,181,98,.08); }
    .pill.k-good { color: var(--moss); border-color: rgba(93,199,148,.35); }
    .pill.k-bad { color: var(--rust); border-color: rgba(224,112,95,.35); }
    .pill.k-wait { color: var(--amber); border-color: rgba(227,181,98,.4); }

    .card-actions { display: flex; flex-wrap: wrap; gap: 8px; }

    pre {
      margin: 0; padding: 11px 13px;
      background: #0b0e15; border: 1px solid var(--line-soft); border-radius: 10px;
      font: 400 .76rem/1.55 var(--mono); color: #b6bdd2;
      white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto; max-height: 260px;
    }
    details summary { cursor: pointer; font-size: .8rem; color: var(--muted); user-select: none; }
    details summary:hover { color: var(--iris-strong); }
    details[open] summary { margin-bottom: 8px; }

    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }
    .toolbar input[type="search"] { width: min(340px, 100%); }
    .toolbar .spacer { flex: 1; }
    .check { display: flex; align-items: center; gap: 7px; font-size: .82rem; color: var(--muted); cursor: pointer; white-space: nowrap; }
    .check input { width: auto; accent-color: var(--iris); }

    .empty { padding: 56px 24px; text-align: center; color: var(--muted); display: grid; gap: 10px; justify-items: center; }
    .empty .glyph {
      width: 44px; height: 44px; border-radius: 50%;
      border: 1px solid var(--line);
      background: radial-gradient(circle, rgba(141,162,255,.4) 0 16%, transparent 18%), radial-gradient(circle, rgba(141,162,255,.1), transparent 70%);
    }
    .empty-title { font: 400 1.45rem var(--display); color: var(--text); }
    .empty p { margin: 0; font-size: .88rem; max-width: 420px; }

    /* ---------- modal + toast ---------- */
    .modal-backdrop {
      position: fixed; inset: 0; z-index: 50;
      background: rgba(5,6,10,.66); backdrop-filter: blur(6px);
      display: flex; align-items: center; justify-content: center; padding: 24px;
    }
    .modal-backdrop[hidden] { display: none; }
    .modal {
      width: min(720px, 100%); max-height: 82vh; overflow: auto;
      background: var(--raised); border: 1px solid rgba(141,162,255,.28); border-radius: 18px;
      padding: 20px 22px; box-shadow: 0 32px 90px rgba(0,0,0,.55);
      display: grid; gap: 12px; animation: pop .2s ease both;
    }
    @keyframes pop { from { opacity: 0; transform: scale(.97) translateY(8px); } to { opacity: 1; transform: none; } }
    .modal-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .modal-head h3 { font: 400 1.4rem var(--display); margin: 0; }

    .toast {
      position: fixed; right: 22px; bottom: 22px; z-index: 60;
      background: var(--raised); border: 1px solid var(--line); border-left: 3px solid var(--iris);
      border-radius: 12px; padding: 12px 16px; font-size: .86rem; max-width: 360px;
      box-shadow: 0 18px 48px rgba(0,0,0,.5);
      opacity: 0; transform: translateY(10px); transition: opacity .25s ease, transform .25s ease;
      pointer-events: none;
    }
    .toast.show { opacity: 1; transform: none; }
    .toast.good { border-left-color: var(--moss); }
    .toast.bad { border-left-color: var(--rust); }

    .mono { font-family: var(--mono); }

    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-thumb { background: rgba(148,163,210,.18); border-radius: 6px; border: 2px solid var(--bg); }
    ::-webkit-scrollbar-track { background: transparent; }

    @media (max-width: 920px) {
      .app { grid-template-columns: 1fr; }
      .side { position: static; height: auto; flex-direction: row; flex-wrap: wrap; align-items: center; gap: 14px; }
      nav { flex-direction: row; flex-wrap: wrap; }
      .nav-item.active::before { display: none; }
      .side-foot { margin: 0; flex: 1 1 100%; }
      .cols { grid-template-columns: 1fr; }
      .top { flex-direction: column; align-items: flex-start; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="side">
      <div class="brand">
        <div class="aperture" aria-hidden="true"></div>
        <div>
          <div class="word">Iris</div>
          <div class="brand-sub">console</div>
        </div>
      </div>
      <nav aria-label="Views">
        <button class="nav-item active" data-view="overview">
          <svg viewBox="0 0 16 16"><rect x="1.5" y="1.5" width="5.4" height="5.4" rx="1.2"/><rect x="9.1" y="1.5" width="5.4" height="5.4" rx="1.2"/><rect x="1.5" y="9.1" width="5.4" height="5.4" rx="1.2"/><rect x="9.1" y="9.1" width="5.4" height="5.4" rx="1.2"/></svg>
          Overview
        </button>
        <button class="nav-item" data-view="memory">
          <svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.2"/><circle cx="8" cy="8" r="2.3"/></svg>
          Memory
        </button>
        <button class="nav-item" data-view="reviews">
          <svg viewBox="0 0 16 16"><path d="M8 1.5 13.7 3.6v4c0 3.4-2.4 5.8-5.7 7-3.3-1.2-5.7-3.6-5.7-7v-4z"/><path d="M5.6 8.2 7.4 10l3-3.6"/></svg>
          Review queue
          <span class="nav-badge" id="navReviews"></span>
        </button>
        <button class="nav-item" data-view="runs">
          <svg viewBox="0 0 16 16"><path d="M1.5 8.2h3.2l1.8-4.4 3 8.4 1.8-4h3.2"/></svg>
          Runs
        </button>
        <button class="nav-item" data-view="approvals">
          <svg viewBox="0 0 16 16"><circle cx="5.5" cy="5.5" r="3.6"/><path d="M8.2 8.2 14 14M11 11l1.8-1.8M12.6 12.6l1.6-1.6"/></svg>
          Approvals
          <span class="nav-badge" id="navApprovals"></span>
        </button>
      </nav>
      <div class="side-foot">
        <div class="conn"><span class="dot" id="connDot"></span><span id="connText">checking gateway</span></div>
        <label class="field-label" for="token">Gateway token</label>
        <input id="token" type="password" placeholder="IRIS_GATEWAY_TOKEN" autocomplete="off">
      </div>
    </aside>

    <main>
      <header class="top">
        <div>
          <h1 id="viewTitle">Overview</h1>
          <p id="viewSub"></p>
        </div>
        <div class="top-actions">
          <button class="ghost" id="refresh">Refresh</button>
        </div>
      </header>
      <section id="view" aria-live="polite"></section>
    </main>
  </div>

  <div class="modal-backdrop" id="modal" hidden>
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
      <div class="modal-head">
        <h3 id="modalTitle"></h3>
        <button class="ghost small" id="modalClose">Close</button>
      </div>
      <div id="modalBody"></div>
    </div>
  </div>
  <div class="toast" id="toast" role="status"></div>

  <script>
    "use strict";
    const $ = (sel, el) => (el || document).querySelector(sel);
    const view = $("#view");

    const state = {
      token: localStorage.getItem("irisGatewayToken") || "",
      view: "overview",
      memMode: "facts",
      memQuery: "",
      includeInactive: false,
      runFilter: "",
      online: null,
      agent: "",
    };

    /* ---------------- helpers ---------------- */
    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }
    function escapeAttr(value) { return escapeHtml(value).replaceAll("`", "&#96;"); }
    function shortId(value) { return String(value || "").slice(0, 8); }
    function num(value) { return Number(value || 0).toFixed(2); }
    function kindOf(status) {
      const s = String(status || "").toLowerCase();
      if (s === "running" || s === "queued") return "k-run";
      if (["done", "completed", "approved", "ok", "active"].includes(s)) return "k-good";
      if (["failed", "error", "denied", "rejected"].includes(s)) return "k-bad";
      if (["blocked", "waiting_approval", "pending", "paused"].includes(s)) return "k-wait";
      return "k-mute";
    }
    function human(seconds) {
      if (seconds < 60) return seconds + "s";
      if (seconds < 3600) return Math.floor(seconds / 60) + "m";
      if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
      return Math.floor(seconds / 86400) + "d";
    }
    function timeAgo(value) {
      if (!value) return "–";
      const t = new Date(value).getTime();
      if (Number.isNaN(t)) return String(value);
      const s = Math.round((Date.now() - t) / 1000);
      if (s < -30) return "in " + human(-s);
      if (s < 30) return "just now";
      return human(s) + " ago";
    }
    function emptyState(title, message, glyph) {
      return `<div class="empty">${glyph === false ? "" : '<div class="glyph"></div>'}<div class="empty-title">${escapeHtml(title)}</div><p>${escapeHtml(message || "")}</p></div>`;
    }
    function toast(message, kind) {
      const el = $("#toast");
      el.textContent = message;
      el.className = "toast show" + (kind ? " " + kind : "");
      clearTimeout(el._t);
      el._t = setTimeout(() => el.classList.remove("show"), 3200);
    }
    function openModal(title, bodyHtml) {
      $("#modalTitle").textContent = title;
      $("#modalBody").innerHTML = bodyHtml;
      $("#modal").hidden = false;
    }
    function closeModal() { $("#modal").hidden = true; }

    class NoTokenError extends Error {}
    async function api(path, options = {}) {
      if (!state.token) throw new NoTokenError("token required");
      const response = await fetch(path, {
        ...options,
        headers: {
          "Authorization": `Bearer ${state.token}`,
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
      return data;
    }

    /* ---------------- shared fragments ---------------- */
    function lockedHtml() {
      return emptyState(
        "Connect to the gateway",
        "Paste your IRIS_GATEWAY_TOKEN into the field in the sidebar. It is stored locally in this browser only."
      );
    }
    function errorHtml(error) {
      if (error instanceof NoTokenError) return lockedHtml();
      return emptyState("Something went wrong", error.message);
    }
    function runRow(run) {
      const status = run.status || "";
      const title = run.message || run.current_step || "Run " + shortId(run.run_id);
      const cancellable = status === "running" || status === "queued";
      return `<div class="row">
        <span class="dot ${kindOf(status)}"></span>
        <div class="row-main">
          <div class="row-title">${escapeHtml(title)}</div>
          <div class="row-meta">${shortId(run.run_id)} · ${escapeHtml(run.channel || "–")} · ${escapeHtml(status)}${run.active_tool ? " · " + escapeHtml(run.active_tool) : ""}</div>
        </div>
        <div class="row-side">
          <span class="row-time">${timeAgo(run.updated_at || run.started_at)}</span>
          ${cancellable ? `<button class="btn-danger small" data-cancel-run="${escapeAttr(run.run_id)}">Cancel</button>` : ""}
        </div>
      </div>`;
    }

    /* ---------------- views ---------------- */
    const VIEWS = {
      overview: {
        title: "Overview",
        sub: "What the agent is doing right now, and what is waiting on you.",
        load: loadOverview,
      },
      memory: {
        title: "Memory",
        sub: "Search the fact graph, trace evidence, and tune lifecycle state.",
        load: loadMemory,
      },
      reviews: {
        title: "Review queue",
        sub: "Sensitive memory candidates held until you approve, supersede, or reject them.",
        load: loadReviews,
      },
      runs: {
        title: "Runs",
        sub: "Everything the orchestrator has executed recently.",
        load: loadRuns,
      },
      approvals: {
        title: "Approvals",
        sub: "Actions paused mid-run until you decide.",
        load: loadApprovals,
      },
    };

    async function loadOverview() {
      const [recent, running, tasks, approvals, reviews, sessions] = await Promise.all([
        api("/runs?limit=8"),
        api("/runs?status=running&limit=50"),
        api("/tasks?limit=50"),
        api("/approvals"),
        api("/memory/reviews?status=pending&limit=50"),
        api("/sessions?limit=6"),
      ]);
      const open = (tasks.tasks || []).filter((t) =>
        ["pending", "running", "waiting_approval", "blocked"].includes(String(t.status || "").toLowerCase())
      ).length;
      const nApprovals = (approvals.approvals || []).length;
      const nReviews = (reviews.items || []).length;
      setBadges(nReviews, nApprovals);

      const stat = (label, value, kind, go) => `
        <button class="stat" data-go="${go}">
          <span class="stat-label">${label}</span>
          <span class="stat-value ${kind}">${value}</span>
          <span class="stat-hint">open ↗</span>
        </button>`;

      const recentRuns = recent.runs || [];
      const sessionList = sessions.sessions || [];
      view.innerHTML = `
        <div class="stats">
          ${stat("Active runs", (running.runs || []).length, "k-run", "runs")}
          ${stat("Pending approvals", nApprovals, nApprovals ? "k-wait" : "", "approvals")}
          ${stat("Review queue", nReviews, nReviews ? "k-wait" : "", "reviews")}
          ${stat("Open tasks", open, "", "runs")}
        </div>
        <div class="cols">
          <section class="panel">
            <div class="panel-head"><h2>Recent runs</h2><button class="ghost small" data-go="runs">View all</button></div>
            ${recentRuns.length ? recentRuns.map(runRow).join("") : emptyState("No runs yet", "Send a message through the gateway to start one.", false)}
          </section>
          <section class="panel">
            <div class="panel-head"><h2>Sessions</h2></div>
            ${sessionList.length ? sessionList.map((s) => `
              <div class="row">
                <span class="dot ${kindOf(s.status)}"></span>
                <div class="row-main">
                  <div class="row-title">${escapeHtml(s.title || "Untitled session")}</div>
                  <div class="row-meta">${shortId(s.session_id)} · ${escapeHtml(s.channel || "–")}</div>
                </div>
                <div class="row-side"><span class="row-time">${timeAgo(s.updated_at)}</span></div>
              </div>`).join("") : emptyState("No sessions", "", false)}
          </section>
        </div>`;
    }

    async function loadMemory() {
      view.innerHTML = `
        <div class="toolbar">
          <input id="memQuery" type="search" placeholder="Search facts or enter an entity…" value="${escapeAttr(state.memQuery)}">
          <div class="chips" id="memMode">
            <button class="chip ${state.memMode === "facts" ? "active" : ""}" data-mem-mode="facts">Facts</button>
            <button class="chip ${state.memMode === "related" ? "active" : ""}" data-mem-mode="related">Related graph</button>
          </div>
          <label class="check"><input type="checkbox" id="memInactive" ${state.includeInactive ? "checked" : ""}> include inactive</label>
          <div class="spacer"></div>
          <button class="ghost" id="memMaintain">Run maintenance</button>
        </div>
        <div class="cards" id="memResults"></div>`;
      $("#memQuery").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          state.memQuery = $("#memQuery").value.trim();
          loadMemoryResults();
        }
      });
      $("#memInactive").addEventListener("change", () => {
        state.includeInactive = $("#memInactive").checked;
        loadMemoryResults();
      });
      $("#memMaintain").addEventListener("click", async () => {
        try {
          const data = await api("/memory/maintain", { method: "POST", body: "{}" });
          toast(`Maintenance done: ${data.relations || 0} relation(s), ${data.stale || 0} stale.`, "good");
          loadMemoryResults();
        } catch (error) { toast(error.message, "bad"); }
      });
      await loadMemoryResults();
    }

    async function loadMemoryResults() {
      const target = $("#memResults");
      if (!target) return;
      try {
        const q = encodeURIComponent(state.memQuery);
        const inactive = state.includeInactive ? "true" : "false";
        let facts = [];
        if (state.memMode === "related") {
          if (!state.memQuery) {
            target.innerHTML = emptyState("Pick an entity", "Type an entity name and press Enter to walk its graph neighbourhood.");
            return;
          }
          const data = await api(`/memory/related?entity=${q}&limit=30&include_inactive=${inactive}`);
          facts = data.facts || [];
        } else {
          const data = await api(`/memory/facts?query=${q}&limit=30&include_inactive=${inactive}`);
          facts = data.facts || [];
        }
        if (!facts.length) {
          target.innerHTML = emptyState("Nothing here", state.memQuery ? "No facts match that query." : "The memory graph is empty so far.");
          return;
        }
        target.innerHTML = facts.map((fact, index) => {
          const conf = Math.max(0, Math.min(1, Number(fact.confidence || 0)));
          return `<article class="card" style="animation-delay:${index * 30}ms">
            <div class="fact-line"><strong>${escapeHtml(fact.subject)}</strong> <span class="pred">${escapeHtml(String(fact.predicate || "").replaceAll("_", " "))}</span> ${escapeHtml(fact.object_value)}</div>
            <div class="conf" title="confidence ${num(fact.confidence)}"><i style="width:${Math.round(conf * 100)}%"></i></div>
            <div class="pills">
              <span class="pill ${fact.active ? "k-good" : ""}">${fact.active ? "active" : "inactive"}</span>
              <span class="pill">conf ${num(fact.confidence)}</span>
              <span class="pill">decay ${num(fact.decay_score)}</span>
              <span class="pill">access ${fact.access_count || 0}</span>
              ${fact.pinned ? '<span class="pill on">pinned</span>' : ""}
            </div>
            ${(() => {
              const body = fact.content || fact.relation_content || "";
              return body && body !== fact.object_value ? `<div style="font-size:.86rem;color:var(--muted)">${escapeHtml(body)}</div>` : "";
            })()}
            <div class="card-actions">
              <button class="ghost small" data-pin="${escapeAttr(fact.relation_id)}" data-pinned="${fact.pinned ? "false" : "true"}">${fact.pinned ? "Unpin" : "Pin"}</button>
              <button class="ghost small" data-evidence="${escapeAttr(fact.object_value)}">Evidence</button>
            </div>
          </article>`;
        }).join("");
      } catch (error) {
        target.innerHTML = errorHtml(error);
      }
    }

    async function loadReviews() {
      const data = await api("/memory/reviews?status=pending&limit=50");
      const items = data.items || [];
      setBadges(items.length, null);
      if (!items.length) {
        view.innerHTML = emptyState("Queue is clear", "No sensitive memory candidates are waiting for review.");
        return;
      }
      view.innerHTML = `<div class="cards">${items.map((item, index) => {
        const c = item.candidate || {};
        return `<article class="card" style="animation-delay:${index * 30}ms">
          <div class="fact-line"><strong>${escapeHtml(c.subject || "candidate")}</strong> <span class="pred">${escapeHtml(String(c.predicate || "").replaceAll("_", " "))}</span> ${escapeHtml(c.object_value || "")}</div>
          <div class="pills">
            <span class="pill k-wait">${escapeHtml(item.reason || "needs review")}</span>
            <span class="pill">${shortId(item.review_id)}</span>
          </div>
          <details><summary>Candidate payload</summary><pre>${escapeHtml(JSON.stringify(c, null, 2))}</pre></details>
          <div class="card-actions">
            <button class="btn-good small" data-decide="${escapeAttr(item.review_id)}" data-decision="approve">Approve</button>
            <button class="ghost small" data-decide="${escapeAttr(item.review_id)}" data-decision="supersede">Supersede</button>
            <button class="btn-danger small" data-decide="${escapeAttr(item.review_id)}" data-decision="reject">Reject</button>
          </div>
        </article>`;
      }).join("")}</div>`;
    }

    async function loadRuns() {
      const filter = state.runFilter;
      const data = await api(`/runs?limit=50${filter ? "&status=" + encodeURIComponent(filter) : ""}`);
      const runs = data.runs || [];
      const chip = (label, value) => `<button class="chip ${filter === value ? "active" : ""}" data-run-filter="${value}">${label}</button>`;
      view.innerHTML = `
        <div class="toolbar">
          <div class="chips">
            ${chip("All", "")}${chip("Running", "running")}${chip("Done", "done")}${chip("Blocked", "blocked")}${chip("Failed", "failed")}${chip("Cancelled", "cancelled")}
          </div>
        </div>
        <section class="panel">
          ${runs.length ? runs.map(runRow).join("") : emptyState("No runs", filter ? "Nothing with that status." : "Send a message through the gateway to start one.")}
        </section>`;
    }

    async function loadApprovals() {
      const data = await api("/approvals");
      const approvals = data.approvals || [];
      setBadges(null, approvals.length);
      if (!approvals.length) {
        view.innerHTML = emptyState("Nothing to approve", "When a run hits a sensitive action it will pause here and wait for you.");
        return;
      }
      view.innerHTML = `<div class="cards">${approvals.map((a, index) => `
        <article class="card" style="animation-delay:${index * 30}ms">
          <div class="fact-line"><strong>${escapeHtml(a.action_name || "action")}</strong></div>
          <div class="pills">
            <span class="pill ${a.risk === "high" || a.risk === "sensitive" ? "k-bad" : "k-wait"}">risk: ${escapeHtml(a.risk || "unknown")}</span>
            <span class="pill">run ${shortId(a.run_id)}</span>
            <span class="pill">asked ${timeAgo(a.created_at)}</span>
            ${a.expires_at ? `<span class="pill">expires ${timeAgo(a.expires_at)}</span>` : ""}
          </div>
          ${a.preview ? `<pre>${escapeHtml(a.preview)}</pre>` : ""}
          <div class="card-actions">
            <button class="btn-good small" data-approval="${escapeAttr(a.approval_id)}" data-decision="approve">Approve</button>
            <button class="btn-danger small" data-approval="${escapeAttr(a.approval_id)}" data-decision="deny">Deny</button>
          </div>
        </article>`).join("")}</div>`;
    }

    /* ---------------- actions ---------------- */
    view.addEventListener("click", async (event) => {
      const go = event.target.closest("[data-go]");
      if (go) { setView(go.dataset.go); return; }

      const memMode = event.target.closest("[data-mem-mode]");
      if (memMode) {
        state.memMode = memMode.dataset.memMode;
        const queryEl = $("#memQuery");
        if (queryEl) state.memQuery = queryEl.value.trim();
        document.querySelectorAll("[data-mem-mode]").forEach((el) => el.classList.toggle("active", el === memMode));
        loadMemoryResults();
        return;
      }
      const runFilter = event.target.closest("[data-run-filter]");
      if (runFilter) { state.runFilter = runFilter.dataset.runFilter; loadView(); return; }

      const pinBtn = event.target.closest("[data-pin]");
      if (pinBtn) {
        try {
          const pinned = pinBtn.dataset.pinned === "true";
          await api(`/memory/facts/${pinBtn.dataset.pin}/${pinned ? "pin" : "unpin"}`, { method: "POST", body: "{}" });
          toast(pinned ? "Fact pinned." : "Fact unpinned.", "good");
          loadMemoryResults();
        } catch (error) { toast(error.message, "bad"); }
        return;
      }
      const evidenceBtn = event.target.closest("[data-evidence]");
      if (evidenceBtn) { showEvidence(evidenceBtn.dataset.evidence); return; }

      const decideBtn = event.target.closest("[data-decide]");
      if (decideBtn) {
        try {
          await api(`/memory/reviews/${decideBtn.dataset.decide}/${decideBtn.dataset.decision}`, { method: "POST", body: "{}" });
          toast(`Review ${decideBtn.dataset.decision}d.`, "good");
          loadView();
        } catch (error) { toast(error.message, "bad"); }
        return;
      }
      const approvalBtn = event.target.closest("[data-approval]");
      if (approvalBtn) {
        try {
          await api(`/approvals/${approvalBtn.dataset.approval}/${approvalBtn.dataset.decision}`, { method: "POST", body: "{}" });
          toast(`Approval ${approvalBtn.dataset.decision === "approve" ? "granted" : "denied"}.`, "good");
          loadView();
        } catch (error) { toast(error.message, "bad"); }
        return;
      }
      const cancelBtn = event.target.closest("[data-cancel-run]");
      if (cancelBtn) {
        if (!confirm("Cancel this run?")) return;
        try {
          await api(`/runs/${cancelBtn.dataset.cancelRun}/cancel`, { method: "POST", body: "{}" });
          toast("Cancellation requested.", "good");
          loadView();
        } catch (error) { toast(error.message, "bad"); }
      }
    });

    async function showEvidence(query) {
      try {
        const data = await api(`/memory/explain?query=${encodeURIComponent(query)}`);
        const fact = data.fact;
        if (!fact) { toast("No evidence found.", "bad"); return; }
        openModal("Evidence", `
          <div class="fact-line">${escapeHtml(fact.relation_content || fact.content || "")}</div>
          <div class="pills" style="margin:10px 0">
            <span class="pill ${fact.active ? "k-good" : ""}">${fact.active ? "active" : "inactive"}</span>
            ${fact.provenance ? `<span class="pill">${escapeHtml(fact.provenance)}</span>` : ""}
          </div>
          ${(fact.evidence || []).map((ev) => `<pre style="margin-bottom:8px">${escapeHtml(JSON.stringify(ev, null, 2))}</pre>`).join("") || "<p style='color:var(--muted)'>No evidence entries recorded.</p>"}
        `);
      } catch (error) { toast(error.message, "bad"); }
    }

    /* ---------------- shell ---------------- */
    function setBadges(reviews, approvals) {
      if (reviews !== null && reviews !== undefined) $("#navReviews").textContent = reviews > 0 ? String(reviews) : "";
      if (approvals !== null && approvals !== undefined) $("#navApprovals").textContent = approvals > 0 ? String(approvals) : "";
    }
    async function refreshBadges() {
      if (!state.token) return;
      try {
        const [approvals, reviews] = await Promise.all([
          api("/approvals"),
          api("/memory/reviews?status=pending&limit=50"),
        ]);
        setBadges((reviews.items || []).length, (approvals.approvals || []).length);
      } catch { /* badge refresh is best-effort */ }
    }

    function setView(name) {
      state.view = name;
      document.querySelectorAll(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.view === name));
      const meta = VIEWS[name];
      $("#viewTitle").textContent = meta.title;
      $("#viewSub").textContent = meta.sub;
      loadView();
    }

    async function loadView(silent) {
      const meta = VIEWS[state.view];
      if (!state.token) { view.innerHTML = lockedHtml(); return; }
      if (!silent && !view.children.length) {
        view.innerHTML = emptyState("Syncing", "Talking to the gateway…", false);
      }
      try {
        await meta.load();
      } catch (error) {
        view.innerHTML = errorHtml(error);
      }
    }

    async function ping() {
      try {
        const response = await fetch("/health");
        const data = await response.json();
        state.online = response.ok;
        state.agent = data.agent || "";
      } catch {
        state.online = false;
      }
      $("#connDot").className = "dot " + (state.online ? "k-good" : "k-bad");
      $("#connText").textContent = state.online ? `online${state.agent ? " · " + state.agent : ""}` : "gateway offline";
    }

    document.querySelectorAll(".nav-item").forEach((el) =>
      el.addEventListener("click", () => setView(el.dataset.view))
    );
    $("#refresh").addEventListener("click", () => loadView());
    $("#modalClose").addEventListener("click", closeModal);
    $("#modal").addEventListener("click", (event) => { if (event.target === $("#modal")) closeModal(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeModal(); });

    const tokenInput = $("#token");
    tokenInput.value = state.token;
    let tokenTimer = null;
    tokenInput.addEventListener("input", () => {
      state.token = tokenInput.value.trim();
      localStorage.setItem("irisGatewayToken", state.token);
      clearTimeout(tokenTimer);
      tokenTimer = setTimeout(() => { loadView(); refreshBadges(); }, 450);
    });

    setInterval(() => {
      if (document.hidden || !$("#modal").hidden) return;
      if (["overview", "runs", "approvals"].includes(state.view)) loadView(true);
      refreshBadges();
    }, 15000);
    setInterval(ping, 10000);

    ping();
    setView("overview");
    refreshBadges();
  </script>
</body>
</html>
"""
