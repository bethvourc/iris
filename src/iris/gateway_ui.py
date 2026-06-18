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
  <link href="https://fonts.googleapis.com/css2?family=Spline+Sans:wght@400;500;600&family=Spline+Sans+Mono:wght@400;500;600&family=Manrope:wght@500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #f4f5f7;
      --panel: #ffffff;
      --raised: #f6f7f9;
      --line: #e1e3e9;
      --line-soft: #e9ebef;
      --text: #181a20;
      --muted: #5f6470;
      --faint: #9095a0;
      --accent: #3d5be6;
      --accent-deep: #2e4ccc;
      --accent-strong: #3d5be6;
      --moss: #2a9d68;
      --amber: #a87b1e;
      --rust: #c2483a;
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
    :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
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
    .btn-good { background: rgba(42,157,104,.1); border-color: rgba(42,157,104,.35); color: var(--moss); padding: 7px 14px; font-size: .85rem; }
    .btn-good:hover { background: rgba(42,157,104,.16); }
    .btn-danger { background: rgba(194,72,58,.08); border-color: rgba(194,72,58,.32); color: var(--rust); padding: 7px 14px; font-size: .85rem; }
    .btn-danger:hover { background: rgba(194,72,58,.14); }
    .small { padding: 4px 10px; font-size: .78rem; }

    .icon-btn {
      width: 32px; height: 32px; flex: none;
      display: inline-grid; place-items: center;
      background: transparent; border: 0; border-radius: 8px; color: var(--muted);
      padding: 0;
    }
    .icon-btn:hover { background: rgba(20,24,36,.06); color: var(--text); }
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
      width: min(420px, 100%);
      margin: clamp(48px, 16vh, 170px) auto 0;
      display: grid; gap: 14px;
      font-family: "Manrope", var(--sans);
      animation: rise .25s ease-out both;
    }
    .gate-body h1 {
      margin: 0 0 16px;
      font: 700 clamp(1.5rem, 3.5vw, 1.85rem)/1.15 "Manrope", var(--sans);
      letter-spacing: -.025em; color: #14161b;
      text-align: center;
    }
    .gate-input { position: relative; }
    .gate-input input {
      background: #fff;
      border: 1px solid #e1e3e9;
      border-radius: 999px;
      color: #181a20;
      font: 500 .92rem "Manrope", var(--sans);
      padding: 14px 54px 14px 22px;
      transition: border-color .15s ease, box-shadow .15s ease;
    }
    .gate-input input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(61,91,230,.12); }
    .gate-input input:focus-visible { outline: none; }
    .gate-input input::placeholder { color: #a7abb5; font-weight: 500; }
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
      border-radius: 999px; padding: 14px 22px;
      font: 600 .95rem "Manrope", var(--sans);
      margin-top: 2px;
    }
    .gate-submit:hover { background: var(--accent-deep); }
    .gate-submit:focus-visible { outline-offset: 3px; }
    .gate-submit:disabled { opacity: .7; cursor: default; }
    .gate-error { min-height: 1.3em; margin: 0; font-size: .85rem; color: #c2483a; text-align: center; }
    .gate-foot { border-top: 1px solid #e4e6eb; padding-top: 18px; margin-top: 2px; }
    .gate-foot p { margin: 0; text-align: center; color: #8b9097; font-size: .85rem; }

    /* ---------- top nav ---------- */
    .nav {
      position: sticky; top: 0; z-index: 20;
      display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 16px;
      padding: 0 clamp(16px, 4vw, 40px);
      height: 56px;
      background: rgba(244,245,247,.88);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--line-soft);
    }
    .tabs { display: flex; align-items: stretch; gap: 2px; height: 56px; overflow-x: auto; scrollbar-width: none; min-width: 0; }
    .tabs::-webkit-scrollbar { display: none; }
    .nav-item {
      display: flex; align-items: center; gap: 7px;
      padding: 0 14px; border: 0; border-radius: 0;
      background: none; color: var(--muted);
      font: 500 .88rem var(--sans); cursor: pointer; white-space: nowrap;
      position: relative; transition: color .15s ease;
    }
    .nav-item:hover { color: var(--text); }
    .nav-item.active { color: var(--text); }
    .nav-item.active::after {
      content: ""; position: absolute; left: 12px; right: 12px; bottom: -1px;
      height: 2px; background: var(--accent);
    }
    .nav-badge {
      font: 600 .66rem var(--mono);
      color: var(--amber); background: rgba(168,123,30,.12);
      border-radius: 4px; padding: 1px 6px;
    }
    .nav-badge:empty { display: none; }
    .nav-right { display: flex; align-items: center; gap: 8px; justify-content: flex-end; }

    /* ---------- main ---------- */
    main { padding: 30px clamp(20px, 4vw, 40px) 64px; max-width: 1160px; width: 100%; margin: 0 auto; }
    .top { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 24px; }
    .top h1 { font: 500 1.5rem/1.2 var(--sans); letter-spacing: -.02em; margin: 0; }
    .top p { margin: 5px 0 0; color: var(--muted); font-size: .88rem; max-width: 580px; }
    .top-actions { display: flex; gap: 8px; flex: none; align-items: center; }

    /* ---------- stats ---------- */
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 10px; }
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

    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr)); gap: 10px; }
    .card { background: var(--panel); border: 1px solid var(--line-soft); border-radius: 8px; padding: 16px; display: grid; gap: 10px; align-content: start; animation: rise .2s ease-out both; }
    .card:hover { border-color: var(--line); }
    @keyframes rise { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

    .fact-line { font-size: .95rem; line-height: 1.5; overflow-wrap: anywhere; }
    .fact-line .pred { font: 500 .8em var(--mono); color: var(--accent-strong); padding: 0 2px; }
    .conf { height: 2px; background: rgba(20,24,36,.08); overflow: hidden; }
    .conf i { display: block; height: 100%; background: var(--accent); }

    .pills { display: flex; flex-wrap: wrap; gap: 6px; }
    .pill { font: 500 .68rem var(--mono); border: 1px solid var(--line); border-radius: 4px; padding: 2px 8px; color: var(--muted); font-variant-numeric: tabular-nums; }
    .pill.on { color: var(--amber); border-color: rgba(168,123,30,.4); }
    .pill.k-good { color: var(--moss); border-color: rgba(42,157,104,.4); }
    .pill.k-bad { color: var(--rust); border-color: rgba(194,72,58,.4); }
    .pill.k-wait { color: var(--amber); border-color: rgba(168,123,30,.4); }

    .card-actions { display: flex; flex-wrap: wrap; gap: 8px; }

    pre {
      margin: 0; padding: 11px 13px;
      background: var(--raised); border: 1px solid var(--line-soft); border-radius: 6px;
      font: 400 .76rem/1.55 var(--mono); color: #4a4e59;
      white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto; max-height: 260px;
    }
    details summary { cursor: pointer; font-size: .8rem; color: var(--muted); user-select: none; }
    details summary:hover { color: var(--accent-strong); }
    details[open] summary { margin-bottom: 8px; }

    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }
    .toolbar input[type="search"] { width: min(340px, 100%); }

    .empty { padding: 56px 24px; text-align: center; color: var(--muted); display: grid; gap: 8px; justify-items: center; }
    .empty-title { font: 500 1.05rem var(--sans); color: var(--text); }
    .empty p { margin: 0; font-size: .88rem; max-width: 420px; }
    .empty-line { padding: 10px 2px 16px; color: var(--faint); font-size: .85rem; }

    .stack { display: grid; gap: 10px; min-width: 0; align-content: start; }

    .kv { margin: 0; display: grid; gap: 7px; padding: 11px 13px; background: var(--raised); border: 1px solid var(--line-soft); border-radius: 6px; }
    .kv-row { display: grid; grid-template-columns: minmax(90px, 34%) 1fr; gap: 12px; align-items: baseline; }
    .kv dt { font: 500 .66rem var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--faint); overflow-wrap: anywhere; }
    .kv dd { margin: 0; font-size: .88rem; color: var(--text); overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }

    .inline-error { font-size: .8rem; color: var(--rust); align-self: center; }

    .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }

    /* ---------- modal ---------- */
    .modal-backdrop {
      position: fixed; inset: 0; z-index: 50;
      background: rgba(15,18,30,.4);
      display: flex; align-items: center; justify-content: center; padding: 24px;
    }
    .modal {
      width: min(720px, 100%); max-height: 82vh; overflow: auto;
      background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
      padding: 20px 22px; box-shadow: 0 24px 64px rgba(15,18,30,.18);
      display: grid; gap: 12px;
    }
    .modal-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .modal-head h3 { font: 500 1.1rem var(--sans); letter-spacing: -.01em; margin: 0; }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
    }

    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-thumb { background: rgba(20,24,36,.18); border-radius: 6px; border: 2px solid var(--bg); }
    ::-webkit-scrollbar-track { background: transparent; }

    @media (max-width: 920px) {
      .cols { grid-template-columns: 1fr; }
    }
    @media (max-width: 720px) {
      .nav { grid-template-columns: auto 1fr auto; gap: 10px; }
      .nav .word { display: none; }
      .nav-item { padding: 0 10px; font-size: .84rem; }
      main { padding-top: 22px; }
      .top { flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom: 18px; }
      .top-actions { position: absolute; right: clamp(16px, 4vw, 40px); margin-top: 2px; }
      .top { position: relative; }
      .toolbar input[type="search"] { width: 100%; }
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
      <div class="gate-input">
        <input id="gateToken" type="password" placeholder="Gateway token" aria-label="Gateway token" autocomplete="current-password" autofocus>
        <button class="gate-eye" type="button" id="gateEye" aria-label="Show token" aria-pressed="false">
          <svg id="eyeClosed" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 10C2 10 6.5 15 12 15C17.5 15 22 10 22 10"/><path d="M12 15v2.6"/><path d="m7.3 14.3-1.4 2.2"/><path d="m16.7 14.3 1.4 2.2"/></svg>
          <svg id="eyeOpen" viewBox="0 0 24 24" aria-hidden="true" hidden><path d="M2 8C2 8 6.47715 3 12 3C17.5228 3 22 8 22 8"/><path d="M21.544 13.045C21.848 13.4713 22 13.6845 22 14C22 14.3155 21.848 14.5287 21.544 14.955C20.1779 16.8706 16.6892 21 12 21C7.31078 21 3.8221 16.8706 2.45604 14.955C2.15201 14.5287 2 14.3155 2 14C2 13.6845 2.15201 13.4713 2.45604 13.045C3.8221 11.1294 7.31078 7 12 7C16.6892 7 20.1779 11.1294 21.544 13.045Z"/><path d="M15 14C15 12.3431 13.6569 11 12 11C10.3431 11 9 12.3431 9 14C9 15.6569 10.3431 17 12 17C13.6569 17 15 15.6569 15 14Z"/></svg>
        </button>
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
        <button class="nav-item active" data-view="overview">Overview</button>
        <button class="nav-item" data-view="memory">Memory</button>
        <button class="nav-item" data-view="reviews">Review queue<span class="nav-badge" id="navReviews"></span></button>
        <button class="nav-item" data-view="runs">Runs</button>
        <button class="nav-item" data-view="approvals">Approvals<span class="nav-badge" id="navApprovals"></span></button>
      </nav>
      <div class="nav-right">
        <button class="icon-btn" id="signOut" title="Sign out" aria-label="Sign out">
          <svg viewBox="0 0 24 24"><path d="M15.5 8.04045C15.4588 6.87972 15.3216 6.15451 14.8645 5.58671C14.2114 4.77536 13.0944 4.52064 10.8605 4.01121L9.85915 3.78286C6.4649 3.00882 4.76777 2.6218 3.63388 3.51317C2.5 4.40454 2.5 6.1257 2.5 9.56803V14.432C2.5 17.8743 2.5 19.5955 3.63388 20.4868C4.76777 21.3782 6.4649 20.9912 9.85915 20.2171L10.8605 19.9888C13.0944 19.4794 14.2114 19.2246 14.8645 18.4133C15.3216 17.8455 15.4588 17.1203 15.5 15.9595"/><path d="M18.5 9.01172C18.5 9.01172 21.5 11.2212 21.5 12.0117C21.5 12.8023 18.5 15.0117 18.5 15.0117M21 12.0117H8.49998"/></svg>
        </button>
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
    function emptyRow(message) {
      return `<div class="empty-line">${escapeHtml(message)}</div>`;
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
      $("#eyeClosed").toggleAttribute("hidden", false);
      $("#eyeOpen").toggleAttribute("hidden", true);
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
      $("#eyeClosed").toggleAttribute("hidden", show);
      $("#eyeOpen").toggleAttribute("hidden", !show);
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
            </div>`).join("") : emptyRow("All clear, no approvals are waiting on you.")}
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
            ${recentRuns.length ? recentRuns.map(runRow).join("") : emptyRow("No runs yet — send a message through the gateway to start one.")}
          </section>
          <div class="stack">
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
                </div>`).join("") : emptyRow("No sessions yet.")}
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
          </div>
        </div>
        <div class="cards" id="memResults"></div>`;
      $("#memQuery").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          state.memQuery = $("#memQuery").value.trim();
          loadMemoryResults();
        }
      });
      await loadMemoryResults();
    }

    async function loadMemoryResults() {
      const target = $("#memResults");
      if (!target) return;
      try {
        const q = encodeURIComponent(state.memQuery);
        let facts = [];
        if (state.memMode === "related") {
          if (!state.memQuery) {
            target.innerHTML = emptyState("Pick an entity", "Type an entity name and press Enter to walk its graph neighbourhood.");
            return;
          }
          const data = await api(`/memory/related?entity=${q}&limit=30`);
          facts = data.facts || [];
        } else {
          const data = await api(`/memory/facts?query=${q}&limit=30`);
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
