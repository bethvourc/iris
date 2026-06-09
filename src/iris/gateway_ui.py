"""Embedded HTML for the Iris gateway dashboard.

Served at /dashboard (and /memory-browser for backwards compatibility).
Single-file vanilla JS app; the user signs in with the gateway bearer
token, which is then used for every JSON API call.
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Iris</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%233d5be6' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'><circle cx='12' cy='12' r='10'/><path d='M11.78 14C10.45 15.39 8.57 17 7 17a5 5 0 1 1 0-10c5.09 0 6.54 8.5 11.52 8.5A3.48 3.48 0 0 0 22 12a3.48 3.48 0 0 0-3.48-3.5c-.9 0-2.05.76-3.02 1.57'/></svg>">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Spline+Sans:wght@400;500;600&family=Spline+Sans+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0a0b0d;
      --panel: #0e0f12;
      --raised: #111216;
      --line: rgba(255,255,255,.09);
      --line-soft: rgba(255,255,255,.055);
      --text: #ededef;
      --muted: #9a9ca6;
      --faint: #6b6d76;
      --accent: #3d5be6;
      --accent-deep: #2e4ccc;
      --accent-strong: #8094ff;
      --moss: #4fbf8b;
      --amber: #d9a94e;
      --rust: #d96b59;
      --sans: "Spline Sans", "Avenir Next", "Segoe UI", sans-serif;
      --mono: "Spline Sans Mono", ui-monospace, "SF Mono", Menlo, monospace;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; }
    body {
      min-height: 100vh;
      color: var(--text);
      font: 400 15px/1.5 var(--sans);
      background: var(--bg);
    }
    ::selection { background: rgba(61,91,230,.35); }
    :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
    [hidden] { display: none !important; }

    .brand { display: flex; align-items: center; gap: 9px; }
    .logo-mark { flex: none; stroke: currentColor; fill: none; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
    .brand .logo-mark { width: 24px; height: 24px; color: var(--accent-strong); }
    .word { font: 600 1.05rem/1.1 var(--sans); letter-spacing: -.01em; }

    input, select {
      width: 100%;
      background: var(--raised);
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--text);
      font: 400 .88rem var(--sans);
      padding: 8px 11px;
      outline: none;
      transition: border-color .15s ease;
    }
    input:focus, select:focus { border-color: var(--accent); }
    input::placeholder { color: var(--faint); }

    button { font: inherit; cursor: pointer; border-radius: 6px; border: 1px solid transparent; transition: background .15s ease, border-color .15s ease, color .15s ease; }
    .primary { background: var(--accent); color: #fff; font-weight: 500; padding: 8px 16px; }
    .primary:hover { background: var(--accent-deep); }
    .ghost { background: transparent; border-color: var(--line); color: var(--text); padding: 7px 14px; font-size: .85rem; }
    .ghost:hover { border-color: var(--accent); }
    .btn-good { background: rgba(79,191,139,.1); border-color: rgba(79,191,139,.32); color: var(--moss); padding: 7px 14px; font-size: .85rem; }
    .btn-good:hover { background: rgba(79,191,139,.18); }
    .btn-danger { background: rgba(217,107,89,.09); border-color: rgba(217,107,89,.3); color: var(--rust); padding: 7px 14px; font-size: .85rem; }
    .btn-danger:hover { background: rgba(217,107,89,.16); }
    .small { padding: 4px 10px; font-size: .78rem; }

    .icon-btn {
      width: 32px; height: 32px; flex: none;
      display: inline-grid; place-items: center;
      background: transparent; border-color: var(--line); color: var(--muted);
      padding: 0;
    }
    .icon-btn:hover { border-color: var(--accent); color: var(--text); }
    .icon-btn svg { width: 15px; height: 15px; stroke: currentColor; fill: none; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
    .icon-btn.busy svg { animation: spin .7s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ---------- sign-in gate (light) ---------- */
    .gate {
      min-height: 100vh;
      background: #f4f5f7;
      color: #181a20;
      display: flex; flex-direction: column;
      padding: 30px clamp(24px, 5vw, 56px) 48px;
    }
    .gate-top {
      display: flex; align-items: center; gap: 10px;
      font: 600 1.3rem/1 var(--sans); letter-spacing: -.02em; color: #181a20;
    }
    .gate-top .logo-mark { width: 30px; height: 30px; color: var(--accent); }
    .gate-body {
      width: min(460px, 100%);
      margin: clamp(48px, 13vh, 140px) auto 0;
      display: grid; gap: 18px;
      animation: rise .25s ease-out both;
    }
    .gate-body h1 {
      margin: 0 0 10px;
      font: 600 clamp(2rem, 5vw, 2.6rem)/1.1 var(--sans);
      letter-spacing: -.03em; color: #14161b;
    }
    .gate-field { display: grid; gap: 9px; }
    .gate-label { font: 600 .92rem var(--sans); color: #23252b; }
    .gate-input { position: relative; }
    .gate-input input {
      background: #fff;
      border: 1px solid #e1e3e9;
      border-radius: 999px;
      color: #181a20;
      font: 400 .95rem var(--sans);
      padding: 15px 54px 15px 22px;
      transition: border-color .15s ease, box-shadow .15s ease;
    }
    .gate-input input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(61,91,230,.12); }
    .gate-input input:focus-visible { outline: none; }
    .gate-input input::placeholder { color: #a7abb5; }
    .gate-eye {
      position: absolute; top: 50%; right: 9px; transform: translateY(-50%);
      width: 38px; height: 38px; border-radius: 50%;
      display: grid; place-items: center;
      background: none; border: 0; color: var(--accent); padding: 0;
    }
    .gate-eye:hover { background: rgba(61,91,230,.08); }
    .gate-eye svg { width: 20px; height: 20px; stroke: currentColor; fill: none; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
    .gate-submit {
      background: var(--accent); color: #fff;
      border-radius: 999px; padding: 15px 22px;
      font: 600 1rem var(--sans);
      margin-top: 4px;
    }
    .gate-submit:hover { background: var(--accent-deep); }
    .gate-submit:disabled { opacity: .7; cursor: default; }
    .gate-error { min-height: 1.3em; margin: 0; font-size: .85rem; color: #c2483a; text-align: center; }
    .gate-foot { border-top: 1px solid #e4e6eb; padding-top: 18px; margin-top: 2px; }
    .gate-foot p { margin: 0; text-align: center; color: #8b9097; font-size: .85rem; }

    /* ---------- top nav ---------- */
    .nav {
      position: sticky; top: 0; z-index: 20;
      display: flex; align-items: center; gap: 24px;
      padding: 0 clamp(20px, 4vw, 40px);
      height: 56px;
      background: rgba(10,11,13,.88);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--line-soft);
    }
    .tabs { display: flex; align-items: stretch; gap: 2px; height: 100%; overflow-x: auto; scrollbar-width: none; }
    .tabs::-webkit-scrollbar { display: none; }
    .nav-item {
      display: flex; align-items: center; gap: 8px;
      padding: 0 13px; border: 0; border-radius: 0;
      background: none; color: var(--muted);
      font: 500 .86rem var(--sans); cursor: pointer; white-space: nowrap;
      position: relative; transition: color .15s ease;
    }
    .nav-item:hover { color: var(--text); }
    .nav-item.active { color: var(--text); }
    .nav-item.active::after {
      content: ""; position: absolute; left: 10px; right: 10px; bottom: -1px;
      height: 2px; background: var(--accent);
    }
    .nav-item svg { width: 15px; height: 15px; stroke: currentColor; fill: none; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; flex: none; }
    .nav-badge {
      font: 600 .66rem var(--mono);
      color: var(--amber); background: rgba(217,169,78,.13);
      border-radius: 4px; padding: 1px 6px;
    }
    .nav-badge:empty { display: none; }
    .nav-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }

    /* ---------- main ---------- */
    main { padding: 30px clamp(20px, 4vw, 40px) 64px; max-width: 1160px; width: 100%; margin: 0 auto; }
    .top { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 24px; }
    .top h1 { font: 500 1.5rem/1.2 var(--sans); letter-spacing: -.02em; margin: 0; }
    .top p { margin: 5px 0 0; color: var(--muted); font-size: .88rem; max-width: 580px; }
    .top-actions { display: flex; gap: 8px; flex: none; align-items: center; }

    /* ---------- stats ---------- */
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 10px; }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      padding: 15px 17px;
      display: grid; gap: 7px;
      text-align: left; color: var(--text); cursor: pointer;
      transition: border-color .15s ease;
    }
    .stat:hover { border-color: var(--accent); }
    .stat-label { font: 500 .64rem var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--faint); }
    .stat-value { font: 500 2rem/1 var(--sans); letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
    .stat-value.k-run { color: var(--accent-strong); }
    .stat-value.k-wait { color: var(--amber); }
    .stat-value.k-good { color: var(--moss); }

    /* ---------- panels, rows, cards ---------- */
    .panel { background: var(--panel); border: 1px solid var(--line-soft); border-radius: 8px; padding: 14px 18px 8px; min-width: 0; }
    .panel-head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; min-height: 30px; }
    .panel-head h2 { font: 500 .68rem var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); margin: 0; }
    .panel-count { font: 500 .68rem var(--mono); color: var(--faint); }
    .panel-head .ghost { margin-left: auto; }
    .cols { display: grid; grid-template-columns: 1.6fr 1fr; gap: 10px; align-items: start; }

    .row {
      display: grid; grid-template-columns: 10px minmax(0, 1fr) auto;
      align-items: center; gap: 12px;
      padding: 11px 0; border-top: 1px solid var(--line-soft);
    }
    .row:first-of-type { border-top: 0; }
    .row-main { min-width: 0; display: grid; gap: 2px; }
    .row-title { font-weight: 500; font-size: .9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row-meta { font: 400 .72rem var(--mono); color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row-side { display: flex; align-items: center; gap: 10px; justify-content: flex-end; }
    .row-time { font: 400 .74rem var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; }

    .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--faint); }
    .dot.k-run { background: var(--accent); animation: pulse 1.6s ease-in-out infinite; }
    .dot.k-good { background: var(--moss); }
    .dot.k-bad { background: var(--rust); }
    .dot.k-wait { background: var(--amber); }
    @keyframes pulse { 50% { opacity: .35; } }

    .chips { display: flex; gap: 6px; flex-wrap: wrap; }
    .chip { background: transparent; border: 1px solid var(--line); border-radius: 6px; color: var(--muted); padding: 5px 12px; font-size: .8rem; }
    .chip:hover { color: var(--text); border-color: var(--accent); }
    .chip.active { background: rgba(61,91,230,.14); border-color: var(--accent); color: var(--accent-strong); }

    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 10px; }
    .card { background: var(--panel); border: 1px solid var(--line-soft); border-radius: 8px; padding: 16px; display: grid; gap: 10px; align-content: start; animation: rise .2s ease-out both; }
    .card:hover { border-color: var(--line); }
    @keyframes rise { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

    .fact-line { font-size: .95rem; line-height: 1.5; overflow-wrap: anywhere; }
    .fact-line .pred { font: 500 .8em var(--mono); color: var(--accent-strong); padding: 0 2px; }
    .conf { height: 2px; background: rgba(255,255,255,.09); overflow: hidden; }
    .conf i { display: block; height: 100%; background: var(--accent); }

    .pills { display: flex; flex-wrap: wrap; gap: 6px; }
    .pill { font: 500 .68rem var(--mono); border: 1px solid var(--line); border-radius: 4px; padding: 2px 8px; color: var(--muted); font-variant-numeric: tabular-nums; }
    .pill.on { color: var(--amber); border-color: rgba(217,169,78,.4); }
    .pill.k-good { color: var(--moss); border-color: rgba(79,191,139,.35); }
    .pill.k-bad { color: var(--rust); border-color: rgba(217,107,89,.35); }
    .pill.k-wait { color: var(--amber); border-color: rgba(217,169,78,.4); }

    .card-actions { display: flex; flex-wrap: wrap; gap: 8px; }

    pre {
      margin: 0; padding: 11px 13px;
      background: var(--raised); border: 1px solid var(--line-soft); border-radius: 6px;
      font: 400 .76rem/1.55 var(--mono); color: #b9bbc4;
      white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto; max-height: 260px;
    }
    details summary { cursor: pointer; font-size: .8rem; color: var(--muted); user-select: none; }
    details summary:hover { color: var(--accent-strong); }
    details[open] summary { margin-bottom: 8px; }

    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }
    .toolbar input[type="search"] { width: min(340px, 100%); }
    .toolbar .spacer { flex: 1; }

    .empty { padding: 56px 24px; text-align: center; color: var(--muted); display: grid; gap: 8px; justify-items: center; }
    .empty-title { font: 500 1.05rem var(--sans); color: var(--text); }
    .empty p { margin: 0; font-size: .88rem; max-width: 420px; }

    .kv { margin: 0; display: grid; gap: 7px; padding: 11px 13px; background: var(--raised); border: 1px solid var(--line-soft); border-radius: 6px; }
    .kv-row { display: grid; grid-template-columns: minmax(90px, 34%) 1fr; gap: 12px; align-items: baseline; }
    .kv dt { font: 500 .66rem var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--faint); overflow-wrap: anywhere; }
    .kv dd { margin: 0; font-size: .88rem; color: var(--text); overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }

    .inline-error { font-size: .8rem; color: var(--rust); align-self: center; }
    .inline-note { font-size: .8rem; color: var(--muted); align-self: center; font-variant-numeric: tabular-nums; }

    .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }

    /* ---------- modal ---------- */
    .modal-backdrop {
      position: fixed; inset: 0; z-index: 50;
      background: rgba(0,0,0,.62);
      display: flex; align-items: center; justify-content: center; padding: 24px;
    }
    .modal {
      width: min(720px, 100%); max-height: 82vh; overflow: auto;
      background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
      padding: 20px 22px; box-shadow: 0 24px 64px rgba(0,0,0,.5);
      display: grid; gap: 12px;
    }
    .modal-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .modal-head h3 { font: 500 1.1rem var(--sans); letter-spacing: -.01em; margin: 0; }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
    }

    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-thumb { background: rgba(255,255,255,.14); border-radius: 6px; border: 2px solid var(--bg); }
    ::-webkit-scrollbar-track { background: transparent; }

    @media (max-width: 920px) {
      .nav { gap: 14px; }
      .nav .word { display: none; }
      .nav-item { padding: 0 9px; }
      .nav-item span.nav-label { display: none; }
      .stats { grid-template-columns: repeat(2, 1fr); }
      .cols { grid-template-columns: 1fr; }
      .top { flex-direction: column; align-items: flex-start; }
      .top-actions { align-self: flex-end; }
    }
  </style>
</head>
<body>
  <!-- sign-in gate -->
  <div class="gate" id="gate" hidden>
    <div class="gate-top">
      <svg class="logo-mark" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M11.7805 14C10.4461 15.3922 8.56592 17 7 17C4.23858 17 2 14.7614 2 12C2 9.23858 4.23858 7 7 7C12.0899 7 13.5399 15.5 18.5217 15.5C20.4427 15.5 22 13.933 22 12C22 10.067 20.4427 8.5 18.5217 8.5C17.6263 8.5 16.4746 9.26045 15.5 10.0724"/></svg>
      Iris
    </div>
    <form class="gate-body" id="gateForm">
      <h1>Log in to continue</h1>
      <div class="gate-field">
        <label class="gate-label" for="gateToken">Gateway token</label>
        <div class="gate-input">
          <input id="gateToken" type="password" placeholder="IRIS_GATEWAY_TOKEN" autocomplete="current-password" autofocus>
          <button class="gate-eye" type="button" id="gateEye" aria-label="Show token" aria-pressed="false">
            <svg id="eyeClosed" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12.5c2.6 2.9 5.7 4.3 9 4.3s6.4-1.4 9-4.3"/><path d="M5.4 15.5 4 17.4"/><path d="m9.5 17.3-.7 2.2"/><path d="m14.5 17.3.7 2.2"/><path d="m18.6 15.5 1.4 1.9"/></svg>
            <svg id="eyeOpen" viewBox="0 0 24 24" aria-hidden="true" hidden><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3.2"/></svg>
          </button>
        </div>
      </div>
      <button class="gate-submit" type="submit" id="gateSubmit">Login</button>
      <p class="gate-error" id="gateError"></p>
      <div class="gate-foot"><p>Your token is stored locally in this browser only.</p></div>
    </form>
  </div>

  <!-- console -->
  <div id="app" hidden>
    <header class="nav">
      <div class="brand">
        <svg class="logo-mark" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M11.7805 14C10.4461 15.3922 8.56592 17 7 17C4.23858 17 2 14.7614 2 12C2 9.23858 4.23858 7 7 7C12.0899 7 13.5399 15.5 18.5217 15.5C20.4427 15.5 22 13.933 22 12C22 10.067 20.4427 8.5 18.5217 8.5C17.6263 8.5 16.4746 9.26045 15.5 10.0724"/></svg>
        <div class="word">Iris</div>
      </div>
      <nav class="tabs" aria-label="Views">
        <!-- Icons: Hugeicons stroke-rounded (hugeicons.com) -->
        <button class="nav-item active" data-view="overview">
          <svg viewBox="0 0 24 24"><path d="M13.6903 19.4567C13.5 18.9973 13.5 18.4149 13.5 17.25C13.5 16.0851 13.5 15.5027 13.6903 15.0433C13.944 14.4307 14.4307 13.944 15.0433 13.6903C15.5027 13.5 16.0851 13.5 17.25 13.5C18.4149 13.5 18.9973 13.5 19.4567 13.6903C20.0693 13.944 20.556 14.4307 20.8097 15.0433C21 15.5027 21 16.0851 21 17.25C21 18.4149 21 18.9973 20.8097 19.4567C20.556 20.0693 20.0693 20.556 19.4567 20.8097C18.9973 21 18.4149 21 17.25 21C16.0851 21 15.5027 21 15.0433 20.8097C14.4307 20.556 13.944 20.0693 13.6903 19.4567Z"/><path d="M13.6903 8.95671C13.5 8.49728 13.5 7.91485 13.5 6.75C13.5 5.58515 13.5 5.00272 13.6903 4.54329C13.944 3.93072 14.4307 3.44404 15.0433 3.1903C15.5027 3 16.0851 3 17.25 3C18.4149 3 18.9973 3 19.4567 3.1903C20.0693 3.44404 20.556 3.93072 20.8097 4.54329C21 5.00272 21 5.58515 21 6.75C21 7.91485 21 8.49728 20.8097 8.95671C20.556 9.56928 20.0693 10.056 19.4567 10.3097C18.9973 10.5 18.4149 10.5 17.25 10.5C16.0851 10.5 15.5027 10.5 15.0433 10.3097C14.4307 10.056 13.944 9.56928 13.6903 8.95671Z"/><path d="M3.1903 19.4567C3 18.9973 3 18.4149 3 17.25C3 16.0851 3 15.5027 3.1903 15.0433C3.44404 14.4307 3.93072 13.944 4.54329 13.6903C5.00272 13.5 5.58515 13.5 6.75 13.5C7.91485 13.5 8.49728 13.5 8.95671 13.6903C9.56928 13.944 10.056 14.4307 10.3097 15.0433C10.5 15.5027 10.5 16.0851 10.5 17.25C10.5 18.4149 10.5 18.9973 10.3097 19.4567C10.056 20.0693 9.56928 20.556 8.95671 20.8097C8.49728 21 7.91485 21 6.75 21C5.58515 21 5.00272 21 4.54329 20.8097C3.93072 20.556 3.44404 20.0693 3.1903 19.4567Z"/><path d="M3.1903 8.95671C3 8.49728 3 7.91485 3 6.75C3 5.58515 3 5.00272 3.1903 4.54329C3.44404 3.93072 3.93072 3.44404 4.54329 3.1903C5.00272 3 5.58515 3 6.75 3C7.91485 3 8.49728 3 8.95671 3.1903C9.56928 3.44404 10.056 3.93072 10.3097 4.54329C10.5 5.00272 10.5 5.58515 10.5 6.75C10.5 7.91485 10.5 8.49728 10.3097 8.95671C10.056 9.56928 9.56928 10.056 8.95671 10.3097C8.49728 10.5 7.91485 10.5 6.75 10.5C5.58515 10.5 5.00272 10.5 4.54329 10.3097C3.93072 10.056 3.44404 9.56928 3.1903 8.95671Z"/></svg>
          <span class="nav-label">Overview</span>
        </button>
        <button class="nav-item" data-view="memory">
          <svg viewBox="0 0 24 24"><path d="M4.22222 21.9948V18.4451C4.22222 17.1737 3.88927 16.5128 3.23482 15.4078C2.4503 14.0833 2 12.5375 2 10.8866C2 5.97866 5.97969 2 10.8889 2C15.7981 2 19.7778 5.97866 19.7778 10.8866C19.7778 11.4663 19.7778 11.7562 19.802 11.9187C19.8598 12.3072 20.0411 12.6414 20.2194 12.9873L22 16.4407L20.6006 17.1402C20.195 17.3429 19.9923 17.4443 19.851 17.6314C19.7097 17.8184 19.67 18.0296 19.5904 18.4519L19.5826 18.4931C19.4004 19.4606 19.1993 20.5286 18.6329 21.2024C18.4329 21.4403 18.1853 21.6336 17.9059 21.7699C17.4447 21.9948 16.8777 21.9948 15.7437 21.9948C15.219 21.9948 14.6928 22.0069 14.1682 21.9942C12.9247 21.9639 12 20.9184 12 19.7044"/><path d="M14.388 10.5315C13.9617 10.5315 13.5729 10.3702 13.2784 10.1048M14.388 10.5315C14.388 11.6774 13.7241 12.7658 12.4461 12.7658C11.1681 12.7658 10.5043 13.8541 10.5043 15M14.388 10.5315C16.5373 10.5315 16.5373 7.18017 14.388 7.18017C14.1927 7.18017 14.0053 7.21403 13.8312 7.27624C13.9362 4.77819 10.3349 4.1 9.51923 6.44018M10.5043 8.29729C10.5043 7.52323 10.1133 6.8411 9.51923 6.44018M9.51923 6.44018C7.66742 5.19034 5.19883 7.4331 6.37324 9.43277C4.40226 9.72827 4.61299 12.7658 6.6205 12.7658C7.18344 12.7658 7.68111 12.4844 7.98234 12.0538"/></svg>
          <span class="nav-label">Memory</span>
        </button>
        <button class="nav-item" data-view="reviews">
          <svg viewBox="0 0 24 24"><path d="M13.498 2H8.49805C7.66962 2 6.99805 2.67157 6.99805 3.5C6.99805 4.32843 7.66962 5 8.49805 5H13.498C14.3265 5 14.998 4.32843 14.998 3.5C14.998 2.67157 14.3265 2 13.498 2Z"/><path d="M6.99805 15H10.4266M6.99805 11H14.998"/><path d="M18.9981 13.5V9.48263C18.9981 6.65424 18.9981 5.24004 18.1194 4.36137C17.4781 3.72007 16.5515 3.54681 14.9981 3.5M11.998 21.9995L8.99805 21.9995C6.16963 21.9995 4.75541 21.9995 3.87674 21.1208C2.99806 20.2421 2.99805 18.8279 2.99805 15.9995L2.99806 9.48269C2.99805 6.65425 2.99805 5.24004 3.87673 4.36136C4.51802 3.72007 5.44456 3.54681 6.99795 3.5"/><path d="M13.998 20C13.998 20 14.998 20 15.998 22C15.998 22 18.1745 17 20.998 16"/></svg>
          <span class="nav-label">Review queue</span>
          <span class="nav-badge" id="navReviews"></span>
        </button>
        <button class="nav-item" data-view="runs">
          <svg viewBox="0 0 24 24"><path d="M4.31802 19.682C3 18.364 3 16.2426 3 12C3 7.75736 3 5.63604 4.31802 4.31802C5.63604 3 7.75736 3 12 3C16.2426 3 18.364 3 19.682 4.31802C21 5.63604 21 7.75736 21 12C21 16.2426 21 18.364 19.682 19.682C18.364 21 16.2426 21 12 21C7.75736 21 5.63604 21 4.31802 19.682Z"/><path d="M7 14L9.79289 11.2071C10.1834 10.8166 10.8166 10.8166 11.2071 11.2071L12.7929 12.7929C13.1834 13.1834 13.8166 13.1834 14.2071 12.7929L17 10"/></svg>
          <span class="nav-label">Runs</span>
        </button>
        <button class="nav-item" data-view="approvals">
          <svg viewBox="0 0 24 24"><path d="M18.9905 19H19M18.9905 19C18.3678 19.6175 17.2393 19.4637 16.4479 19.4637C15.4765 19.4637 15.0087 19.6537 14.3154 20.347C13.7251 20.9374 12.9337 22 12 22C11.0663 22 10.2749 20.9374 9.68457 20.347C8.99128 19.6537 8.52349 19.4637 7.55206 19.4637C6.76068 19.4637 5.63218 19.6175 5.00949 19C4.38181 18.3776 4.53628 17.2444 4.53628 16.4479C4.53628 15.4414 4.31616 14.9786 3.59938 14.2618C2.53314 13.1956 2.00002 12.6624 2 12C2.00001 11.3375 2.53312 10.8044 3.59935 9.73817C4.2392 9.09832 4.53628 8.46428 4.53628 7.55206C4.53628 6.76065 4.38249 5.63214 5 5.00944C5.62243 4.38178 6.7556 4.53626 7.55208 4.53626C8.46427 4.53626 9.09832 4.2392 9.73815 3.59937C10.8044 2.53312 11.3375 2 12 2C12.6625 2 13.1956 2.53312 14.2618 3.59937C14.9015 4.23907 15.5355 4.53626 16.4479 4.53626C17.2393 4.53626 18.3679 4.38247 18.9906 5C19.6182 5.62243 19.4637 6.75559 19.4637 7.55206C19.4637 8.55858 19.6839 9.02137 20.4006 9.73817C21.4669 10.8044 22 11.3375 22 12C22 12.6624 21.4669 13.1956 20.4006 14.2618C19.6838 14.9786 19.4637 15.4414 19.4637 16.4479C19.4637 17.2444 19.6182 18.3776 18.9905 19Z"/><path d="M9 12.8929C9 12.8929 10.2 13.5447 10.8 14.5C10.8 14.5 12.6 10.75 15 9.5"/></svg>
          <span class="nav-label">Approvals</span>
          <span class="nav-badge" id="navApprovals"></span>
        </button>
      </nav>
      <div class="nav-right">
        <button class="ghost small" id="signOut">Sign out</button>
      </div>
    </header>

    <main>
      <header class="top">
        <div>
          <h1 id="viewTitle">Overview</h1>
          <p id="viewSub"></p>
        </div>
        <div class="top-actions">
          <button class="icon-btn" id="refresh" title="Refresh" aria-label="Refresh">
            <svg viewBox="0 0 24 24"><path d="M20.5 5.5V9.5H16.5"/><path d="M3.5 18.5V14.5H7.5"/><path d="M4.33782 9.5C5.40324 6.0094 8.55793 3.5 12.2645 3.5C15.4032 3.5 18.1418 5.29719 19.5571 7.96703M19.6622 14.5C18.5968 17.9906 15.4421 20.5 11.7355 20.5C8.59679 20.5 5.85821 18.7028 4.44293 16.033"/></svg>
          </button>
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

  <script>
    "use strict";
    const $ = (sel, el) => (el || document).querySelector(sel);
    const view = $("#view");

    const state = {
      token: localStorage.getItem("irisGatewayToken") || "",
      authed: false,
      view: "overview",
      memMode: "facts",
      memQuery: "",
      includeInactive: false,
      runFilter: "",
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
    function emptyState(title, message) {
      return `<div class="empty"><div class="empty-title">${escapeHtml(title)}</div><p>${escapeHtml(message || "")}</p></div>`;
    }
    function actionError(anchor, message) {
      const container = anchor.closest(".card-actions, .row-side, .toolbar") || anchor.parentElement;
      let el = container.querySelector(".inline-error");
      if (!el) {
        el = document.createElement("span");
        el.className = "inline-error";
        container.appendChild(el);
      }
      el.textContent = message;
    }
    function parseMaybeJson(value) {
      if (typeof value !== "string") return null;
      const s = value.trim();
      if (!s.startsWith("{") && !s.startsWith("[")) return null;
      try {
        const parsed = JSON.parse(s);
        return parsed && typeof parsed === "object" ? parsed : null;
      } catch { return null; }
    }
    function fmtScalar(value) {
      if (value === null || value === undefined || value === "") return "–";
      if (Array.isArray(value)) return value.map(fmtScalar).join(", ");
      if (typeof value === "object") return Object.entries(value).map(([k, v]) => `${k.replaceAll("_", " ")}: ${fmtScalar(v)}`).join(" · ");
      if (typeof value === "string") {
        const parsed = parseMaybeJson(value);
        if (parsed) return fmtScalar(parsed);
      }
      return String(value);
    }
    function kvHtml(obj) {
      const entries = Array.isArray(obj) ? obj.map((v, i) => [String(i + 1), v]) : Object.entries(obj);
      if (!entries.length) return "";
      return `<dl class="kv">${entries.map(([k, v]) => `
        <div class="kv-row"><dt>${escapeHtml(String(k).replaceAll("_", " "))}</dt><dd>${escapeHtml(fmtScalar(v))}</dd></div>`).join("")}</dl>`;
    }
    function splitEmbeddedJson(text) {
      const s = String(text || "");
      const brace = s.indexOf("{");
      if (brace === -1) return null;
      const parsed = parseMaybeJson(s.slice(brace));
      if (!parsed) return null;
      return { prefix: s.slice(0, brace).trim(), data: parsed };
    }
    function openModal(title, bodyHtml) {
      $("#modalTitle").textContent = title;
      $("#modalBody").innerHTML = bodyHtml;
      $("#modal").hidden = false;
    }
    function closeModal() { $("#modal").hidden = true; }

    class AuthError extends Error {}
    async function api(path, options = {}) {
      if (!state.token) throw new AuthError("token required");
      const response = await fetch(path, {
        ...options,
        headers: {
          "Authorization": `Bearer ${state.token}`,
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
      });
      if (response.status === 401) throw new AuthError("token rejected");
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
      return data;
    }

    /* ---------------- sign-in gate ---------------- */
    function showGate(message) {
      state.authed = false;
      $("#app").hidden = true;
      $("#gate").hidden = false;
      $("#gateError").textContent = message || "";
      const input = $("#gateToken");
      input.value = "";
      input.type = "password";
      $("#eyeClosed").hidden = false;
      $("#eyeOpen").hidden = true;
      input.focus();
    }
    function showApp() {
      state.authed = true;
      $("#gate").hidden = true;
      $("#app").hidden = false;
      setView(state.view);
      refreshBadges();
    }
    async function verifyToken(token) {
      const response = await fetch("/sessions?limit=1", {
        headers: { "Authorization": `Bearer ${token}` },
      });
      return response.ok;
    }
    $("#gateForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const token = $("#gateToken").value.trim();
      if (!token) { $("#gateError").textContent = "Enter a token."; return; }
      const submit = $("#gateSubmit");
      submit.disabled = true;
      submit.textContent = "Checking…";
      try {
        if (await verifyToken(token)) {
          state.token = token;
          localStorage.setItem("irisGatewayToken", token);
          showApp();
        } else {
          $("#gateError").textContent = "That token was rejected by the gateway.";
        }
      } catch {
        $("#gateError").textContent = "Could not reach the gateway. Is it running?";
      } finally {
        submit.disabled = false;
        submit.textContent = "Login";
      }
    });
    $("#gateEye").addEventListener("click", () => {
      const input = $("#gateToken");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      $("#eyeClosed").hidden = show;
      $("#eyeOpen").hidden = !show;
      const eye = $("#gateEye");
      eye.setAttribute("aria-label", show ? "Hide token" : "Show token");
      eye.setAttribute("aria-pressed", String(show));
      input.focus();
    });
    function signOut(message) {
      state.token = "";
      localStorage.removeItem("irisGatewayToken");
      showGate(message);
    }
    $("#signOut").addEventListener("click", () => signOut());

    /* ---------------- shared fragments ---------------- */
    function errorHtml(error) {
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
        api("/sessions?limit=8"),
      ]);
      const open = (tasks.tasks || []).filter((t) =>
        ["pending", "running", "waiting_approval", "blocked"].includes(String(t.status || "").toLowerCase())
      ).length;
      const approvalList = approvals.approvals || [];
      const nApprovals = approvalList.length;
      const nReviews = (reviews.items || []).length;
      setBadges(nReviews, nApprovals);

      const stat = (label, value, kind, go) => `
        <button class="stat" data-go="${go}">
          <span class="stat-label">${label}</span>
          <span class="stat-value ${kind}">${value}</span>
        </button>`;

      const recentRuns = recent.runs || [];
      const sessionList = sessions.sessions || [];
      const attention = `
        <section class="panel">
          <div class="panel-head"><h2>Needs you</h2>${nApprovals ? `<button class="ghost small" data-go="approvals">Open</button>` : ""}</div>
          ${nApprovals ? approvalList.slice(0, 4).map((a) => `
            <div class="row">
              <span class="dot k-wait"></span>
              <div class="row-main">
                <div class="row-title">${escapeHtml(a.action_name || "action")}</div>
                <div class="row-meta">risk ${escapeHtml(a.risk || "unknown")} · run ${shortId(a.run_id)}</div>
              </div>
              <div class="row-side"><span class="row-time">${timeAgo(a.created_at)}</span></div>
            </div>`).join("") : emptyState("All clear", "No approvals are waiting on you.")}
        </section>`;
      view.innerHTML = `
        <div class="stats">
          ${stat("Active runs", (running.runs || []).length, "k-run", "runs")}
          ${stat("Pending approvals", nApprovals, nApprovals ? "k-wait" : "", "approvals")}
          ${stat("Review queue", nReviews, nReviews ? "k-wait" : "", "reviews")}
          ${stat("Open tasks", open, "", "runs")}
        </div>
        <div class="cols">
          <section class="panel">
            <div class="panel-head"><h2>Recent runs</h2><span class="panel-count">${recentRuns.length}</span><button class="ghost small" data-go="runs">View all</button></div>
            ${recentRuns.length ? recentRuns.map(runRow).join("") : emptyState("No runs yet", "Send a message through the gateway to start one.")}
          </section>
          <div style="display:grid;gap:10px;min-width:0">
            ${attention}
            <section class="panel">
              <div class="panel-head"><h2>Sessions</h2><span class="panel-count">${sessionList.length}</span></div>
              ${sessionList.length ? sessionList.map((s) => `
                <div class="row">
                  <span class="dot ${kindOf(s.status)}"></span>
                  <div class="row-main">
                    <div class="row-title">${escapeHtml(s.title || "Untitled session")}</div>
                    <div class="row-meta">${shortId(s.session_id)} · ${escapeHtml(s.channel || "–")}</div>
                  </div>
                  <div class="row-side"><span class="row-time">${timeAgo(s.updated_at)}</span></div>
                </div>`).join("") : emptyState("No sessions", "")}
            </section>
          </div>
        </div>`;
    }

    async function loadMemory() {
      view.innerHTML = `
        <div class="toolbar">
          <input id="memQuery" type="search" placeholder="Search facts or enter an entity…" value="${escapeAttr(state.memQuery)}">
          <div class="chips" id="memMode">
            <button class="chip ${state.memMode === "facts" ? "active" : ""}" data-mem-mode="facts">Facts</button>
            <button class="chip ${state.memMode === "related" ? "active" : ""}" data-mem-mode="related">Related graph</button>
            <button class="chip ${state.includeInactive ? "active" : ""}" id="memInactive" title="Show inactive facts too">Inactive</button>
          </div>
          <div class="spacer"></div>
          <span class="inline-note" id="memNote"></span>
          <button class="ghost" id="memMaintain">Run maintenance</button>
        </div>
        <div class="cards" id="memResults"></div>`;
      $("#memQuery").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          state.memQuery = $("#memQuery").value.trim();
          loadMemoryResults();
        }
      });
      $("#memInactive").addEventListener("click", () => {
        state.includeInactive = !state.includeInactive;
        $("#memInactive").classList.toggle("active", state.includeInactive);
        loadMemoryResults();
      });
      $("#memMaintain").addEventListener("click", async () => {
        const note = $("#memNote");
        try {
          const data = await api("/memory/maintain", { method: "POST", body: "{}" });
          note.textContent = `${data.relations || 0} relation(s) touched, ${data.stale || 0} stale`;
          loadMemoryResults();
        } catch (error) {
          note.textContent = error.message;
          note.classList.add("inline-error");
        }
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
          const structured = parseMaybeJson(fact.object_value);
          const body = fact.content || fact.relation_content || "";
          const bodyStructured = parseMaybeJson(body);
          return `<article class="card" style="animation-delay:${index * 20}ms">
            <div class="fact-line"><strong>${escapeHtml(fact.subject)}</strong> <span class="pred">${escapeHtml(String(fact.predicate || "").replaceAll("_", " "))}</span>${structured ? "" : " " + escapeHtml(fact.object_value)}</div>
            ${structured ? kvHtml(structured) : ""}
            <div class="conf" title="confidence ${num(fact.confidence)}"><i style="width:${Math.round(conf * 100)}%"></i></div>
            <div class="pills">
              <span class="pill ${fact.active ? "k-good" : ""}">${fact.active ? "active" : "inactive"}</span>
              <span class="pill">conf ${num(fact.confidence)}</span>
              <span class="pill">decay ${num(fact.decay_score)}</span>
              <span class="pill">access ${fact.access_count || 0}</span>
              ${fact.pinned ? '<span class="pill on">pinned</span>' : ""}
            </div>
            ${body && body !== fact.object_value ? (bodyStructured ? kvHtml(bodyStructured) : `<div style="font-size:.86rem;color:var(--muted)">${escapeHtml(body)}</div>`) : ""}
            <div class="card-actions">
              <button class="ghost small" data-pin="${escapeAttr(fact.relation_id)}" data-pinned="${fact.pinned ? "false" : "true"}">${fact.pinned ? "Unpin" : "Pin"}</button>
              <button class="ghost small" data-evidence="${escapeAttr(fact.object_value)}">Evidence</button>
            </div>
          </article>`;
        }).join("");
      } catch (error) {
        if (error instanceof AuthError) { signOut("Your token was rejected. Sign in again."); return; }
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
        const objStructured = parseMaybeJson(c.object_value);
        const rest = Object.fromEntries(Object.entries(c).filter(([k, v]) =>
          !["subject", "predicate", "object_value"].includes(k) && v !== null && v !== "" && v !== undefined
        ));
        return `<article class="card" style="animation-delay:${index * 20}ms">
          <div class="fact-line"><strong>${escapeHtml(c.subject || "candidate")}</strong> <span class="pred">${escapeHtml(String(c.predicate || "").replaceAll("_", " "))}</span>${objStructured ? "" : " " + escapeHtml(c.object_value || "")}</div>
          ${objStructured ? kvHtml(objStructured) : ""}
          <div class="pills">
            <span class="pill k-wait">${escapeHtml(item.reason || "needs review")}</span>
            <span class="pill">${shortId(item.review_id)}</span>
          </div>
          ${Object.keys(rest).length ? kvHtml(rest) : ""}
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
        <article class="card" style="animation-delay:${index * 20}ms">
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
          loadMemoryResults();
        } catch (error) { actionError(pinBtn, error.message); }
        return;
      }
      const evidenceBtn = event.target.closest("[data-evidence]");
      if (evidenceBtn) { showEvidence(evidenceBtn, evidenceBtn.dataset.evidence); return; }

      const decideBtn = event.target.closest("[data-decide]");
      if (decideBtn) {
        try {
          await api(`/memory/reviews/${decideBtn.dataset.decide}/${decideBtn.dataset.decision}`, { method: "POST", body: "{}" });
          loadView();
        } catch (error) { actionError(decideBtn, error.message); }
        return;
      }
      const approvalBtn = event.target.closest("[data-approval]");
      if (approvalBtn) {
        try {
          await api(`/approvals/${approvalBtn.dataset.approval}/${approvalBtn.dataset.decision}`, { method: "POST", body: "{}" });
          loadView();
        } catch (error) { actionError(approvalBtn, error.message); }
        return;
      }
      const cancelBtn = event.target.closest("[data-cancel-run]");
      if (cancelBtn) {
        if (!confirm("Cancel this run?")) return;
        try {
          await api(`/runs/${cancelBtn.dataset.cancelRun}/cancel`, { method: "POST", body: "{}" });
          loadView();
        } catch (error) { actionError(cancelBtn, error.message); }
      }
    });

    async function showEvidence(anchor, query) {
      try {
        const data = await api(`/memory/explain?query=${encodeURIComponent(query)}`);
        const fact = data.fact;
        if (!fact) { actionError(anchor, "No evidence found."); return; }
        const headline = fact.relation_content || fact.content || "";
        const embedded = splitEmbeddedJson(headline);
        openModal("Evidence", `
          ${embedded
            ? `${embedded.prefix ? `<div class="fact-line">${escapeHtml(embedded.prefix)}</div>` : ""}${kvHtml(embedded.data)}`
            : `<div class="fact-line">${escapeHtml(headline)}</div>`}
          <div class="pills" style="margin:10px 0">
            <span class="pill ${fact.active ? "k-good" : ""}">${fact.active ? "active" : "inactive"}</span>
            ${fact.provenance ? `<span class="pill">${escapeHtml(fact.provenance)}</span>` : ""}
          </div>
          ${(fact.evidence || []).map((ev) => kvHtml(ev)).join("") || "<p style='color:var(--muted)'>No evidence entries recorded.</p>"}
        `);
      } catch (error) { actionError(anchor, error.message); }
    }

    /* ---------------- shell ---------------- */
    function setBadges(reviews, approvals) {
      if (reviews !== null && reviews !== undefined) $("#navReviews").textContent = reviews > 0 ? String(reviews) : "";
      if (approvals !== null && approvals !== undefined) $("#navApprovals").textContent = approvals > 0 ? String(approvals) : "";
    }
    async function refreshBadges() {
      if (!state.authed) return;
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
      if (!state.authed) return;
      const meta = VIEWS[state.view];
      if (!silent && !view.children.length) {
        view.innerHTML = emptyState("Syncing", "Talking to the gateway…");
      }
      try {
        await meta.load();
      } catch (error) {
        if (error instanceof AuthError) { signOut("Your token was rejected. Sign in again."); return; }
        view.innerHTML = errorHtml(error);
      }
    }

    document.querySelectorAll(".nav-item").forEach((el) =>
      el.addEventListener("click", () => setView(el.dataset.view))
    );
    $("#refresh").addEventListener("click", async () => {
      const btn = $("#refresh");
      btn.classList.add("busy");
      try { await loadView(); } finally { btn.classList.remove("busy"); }
    });
    $("#modalClose").addEventListener("click", closeModal);
    $("#modal").addEventListener("click", (event) => { if (event.target === $("#modal")) closeModal(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeModal(); });

    setInterval(() => {
      if (!state.authed || document.hidden || !$("#modal").hidden) return;
      if (["overview", "runs", "approvals"].includes(state.view)) loadView(true);
      refreshBadges();
    }, 15000);

    /* ---------------- boot ---------------- */
    (async () => {
      if (state.token) {
        try {
          if (await verifyToken(state.token)) { showApp(); return; }
          signOut("Your saved token was rejected. Sign in again.");
          return;
        } catch {
          // gateway unreachable: fall through to the gate with a hint
          showGate("Could not reach the gateway. Is it running?");
          return;
        }
      }
      showGate();
    })();
  </script>
</body>
</html>
"""
