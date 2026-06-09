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
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='13' fill='none' stroke='%235468ff' stroke-width='2.5'/><circle cx='16' cy='16' r='5' fill='%235468ff'/></svg>">
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
      --accent: #5468ff;
      --accent-strong: #7787ff;
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
    ::selection { background: rgba(84,104,255,.4); }
    :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }

    .app { display: grid; grid-template-columns: 230px minmax(0, 1fr); min-height: 100vh; }

    /* ---------- sidebar ---------- */
    .side {
      border-right: 1px solid var(--line-soft);
      padding: 24px 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 28px;
      position: sticky;
      top: 0;
      height: 100vh;
    }
    .brand { display: flex; align-items: center; gap: 11px; padding: 0 8px; }
    .mark {
      width: 28px; height: 28px; border-radius: 50%; flex: none;
      border: 1.5px solid var(--accent);
      display: grid; place-items: center;
    }
    .mark::after { content: ""; width: 9px; height: 9px; border-radius: 50%; background: var(--accent); }
    .word { font: 600 1.05rem/1.1 var(--sans); letter-spacing: -.01em; }
    .brand-sub { font: 500 .6rem var(--mono); letter-spacing: .3em; text-transform: uppercase; color: var(--faint); margin-top: 2px; }

    nav { display: flex; flex-direction: column; gap: 2px; }
    .nav-item {
      display: flex; align-items: center; gap: 11px;
      padding: 8px 11px; border: 0; border-radius: 6px;
      background: none; color: var(--muted);
      font: 500 .88rem var(--sans); text-align: left; cursor: pointer;
      position: relative; transition: color .15s ease, background .15s ease;
    }
    .nav-item:hover { color: var(--text); background: rgba(255,255,255,.05); }
    .nav-item.active { color: var(--text); background: rgba(255,255,255,.07); }
    .nav-item.active::before {
      content: ""; position: absolute; left: -16px; top: 7px; bottom: 7px;
      width: 2px; background: var(--accent);
    }
    .nav-item svg { width: 16px; height: 16px; stroke: currentColor; fill: none; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; flex: none; }
    .nav-badge {
      margin-left: auto; font: 600 .68rem var(--mono);
      color: var(--amber); background: rgba(217,169,78,.13);
      border-radius: 4px; padding: 1px 6px;
    }
    .nav-badge:empty { display: none; }

    .side-foot { margin-top: auto; display: grid; gap: 10px; padding: 0 4px; }
    .conn { display: flex; align-items: center; gap: 8px; font: 500 .72rem var(--mono); color: var(--muted); }
    .field-label { font: 500 .62rem var(--mono); letter-spacing: .16em; text-transform: uppercase; color: var(--faint); }

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

    /* ---------- main ---------- */
    main { padding: 32px clamp(20px, 4vw, 48px) 64px; max-width: 1200px; width: 100%; }
    .top { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 26px; }
    .top h1 { font: 500 1.6rem/1.2 var(--sans); letter-spacing: -.02em; margin: 0; }
    .top p { margin: 6px 0 0; color: var(--muted); font-size: .9rem; max-width: 580px; }
    .top-actions { display: flex; gap: 8px; flex: none; }

    button { font: inherit; cursor: pointer; border-radius: 6px; border: 1px solid transparent; transition: background .15s ease, border-color .15s ease, color .15s ease; }
    .primary { background: var(--accent); color: #fff; font-weight: 500; padding: 8px 16px; }
    .primary:hover { background: var(--accent-strong); }
    .ghost { background: transparent; border-color: var(--line); color: var(--text); padding: 7px 14px; font-size: .85rem; }
    .ghost:hover { border-color: var(--accent); }
    .btn-good { background: rgba(79,191,139,.1); border-color: rgba(79,191,139,.32); color: var(--moss); padding: 7px 14px; font-size: .85rem; }
    .btn-good:hover { background: rgba(79,191,139,.18); }
    .btn-danger { background: rgba(217,107,89,.09); border-color: rgba(217,107,89,.3); color: var(--rust); padding: 7px 14px; font-size: .85rem; }
    .btn-danger:hover { background: rgba(217,107,89,.16); }
    .small { padding: 4px 10px; font-size: .78rem; }

    /* ---------- stats ---------- */
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 10px; margin-bottom: 12px; }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      padding: 16px 18px;
      display: grid; gap: 8px;
      text-align: left; color: var(--text); cursor: pointer;
      transition: border-color .15s ease;
    }
    .stat:hover { border-color: var(--accent); }
    .stat-label { font: 500 .64rem var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--faint); }
    .stat-value { font: 500 2.1rem/1 var(--sans); letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
    .stat-value.k-run { color: var(--accent-strong); }
    .stat-value.k-wait { color: var(--amber); }
    .stat-value.k-good { color: var(--moss); }

    /* ---------- panels, rows, cards ---------- */
    .panel { background: var(--panel); border: 1px solid var(--line-soft); border-radius: 8px; padding: 16px 18px; min-width: 0; }
    .panel-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 8px; }
    .panel-head h2 { font: 500 .68rem var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); margin: 0; }
    .cols { display: grid; grid-template-columns: 1.55fr 1fr; gap: 10px; align-items: start; }

    .row { display: flex; align-items: center; gap: 12px; padding: 11px 2px; border-top: 1px solid var(--line-soft); }
    .row:first-child { border-top: 0; }
    .row-main { min-width: 0; display: grid; gap: 2px; }
    .row-title { font-weight: 500; font-size: .9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row-meta { font: 400 .72rem var(--mono); color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row-side { margin-left: auto; display: flex; align-items: center; gap: 10px; flex: none; }
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
    .chip.active { background: rgba(84,104,255,.12); border-color: var(--accent); color: var(--accent-strong); }

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
    .check { display: flex; align-items: center; gap: 7px; font-size: .82rem; color: var(--muted); cursor: pointer; white-space: nowrap; }
    .check input { width: auto; accent-color: var(--accent); }

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
    .modal-backdrop[hidden] { display: none; }
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
        <div class="mark" aria-hidden="true"></div>
        <div>
          <div class="word">Iris</div>
          <div class="brand-sub">console</div>
        </div>
      </div>
      <nav aria-label="Views">
        <!-- Icons: Hugeicons stroke-rounded (hugeicons.com) -->
        <button class="nav-item active" data-view="overview">
          <svg viewBox="0 0 24 24"><path d="M13.6903 19.4567C13.5 18.9973 13.5 18.4149 13.5 17.25C13.5 16.0851 13.5 15.5027 13.6903 15.0433C13.944 14.4307 14.4307 13.944 15.0433 13.6903C15.5027 13.5 16.0851 13.5 17.25 13.5C18.4149 13.5 18.9973 13.5 19.4567 13.6903C20.0693 13.944 20.556 14.4307 20.8097 15.0433C21 15.5027 21 16.0851 21 17.25C21 18.4149 21 18.9973 20.8097 19.4567C20.556 20.0693 20.0693 20.556 19.4567 20.8097C18.9973 21 18.4149 21 17.25 21C16.0851 21 15.5027 21 15.0433 20.8097C14.4307 20.556 13.944 20.0693 13.6903 19.4567Z"/><path d="M13.6903 8.95671C13.5 8.49728 13.5 7.91485 13.5 6.75C13.5 5.58515 13.5 5.00272 13.6903 4.54329C13.944 3.93072 14.4307 3.44404 15.0433 3.1903C15.5027 3 16.0851 3 17.25 3C18.4149 3 18.9973 3 19.4567 3.1903C20.0693 3.44404 20.556 3.93072 20.8097 4.54329C21 5.00272 21 5.58515 21 6.75C21 7.91485 21 8.49728 20.8097 8.95671C20.556 9.56928 20.0693 10.056 19.4567 10.3097C18.9973 10.5 18.4149 10.5 17.25 10.5C16.0851 10.5 15.5027 10.5 15.0433 10.3097C14.4307 10.056 13.944 9.56928 13.6903 8.95671Z"/><path d="M3.1903 19.4567C3 18.9973 3 18.4149 3 17.25C3 16.0851 3 15.5027 3.1903 15.0433C3.44404 14.4307 3.93072 13.944 4.54329 13.6903C5.00272 13.5 5.58515 13.5 6.75 13.5C7.91485 13.5 8.49728 13.5 8.95671 13.6903C9.56928 13.944 10.056 14.4307 10.3097 15.0433C10.5 15.5027 10.5 16.0851 10.5 17.25C10.5 18.4149 10.5 18.9973 10.3097 19.4567C10.056 20.0693 9.56928 20.556 8.95671 20.8097C8.49728 21 7.91485 21 6.75 21C5.58515 21 5.00272 21 4.54329 20.8097C3.93072 20.556 3.44404 20.0693 3.1903 19.4567Z"/><path d="M3.1903 8.95671C3 8.49728 3 7.91485 3 6.75C3 5.58515 3 5.00272 3.1903 4.54329C3.44404 3.93072 3.93072 3.44404 4.54329 3.1903C5.00272 3 5.58515 3 6.75 3C7.91485 3 8.49728 3 8.95671 3.1903C9.56928 3.44404 10.056 3.93072 10.3097 4.54329C10.5 5.00272 10.5 5.58515 10.5 6.75C10.5 7.91485 10.5 8.49728 10.3097 8.95671C10.056 9.56928 9.56928 10.056 8.95671 10.3097C8.49728 10.5 7.91485 10.5 6.75 10.5C5.58515 10.5 5.00272 10.5 4.54329 10.3097C3.93072 10.056 3.44404 9.56928 3.1903 8.95671Z"/></svg>
          Overview
        </button>
        <button class="nav-item" data-view="memory">
          <svg viewBox="0 0 24 24"><path d="M4.22222 21.9948V18.4451C4.22222 17.1737 3.88927 16.5128 3.23482 15.4078C2.4503 14.0833 2 12.5375 2 10.8866C2 5.97866 5.97969 2 10.8889 2C15.7981 2 19.7778 5.97866 19.7778 10.8866C19.7778 11.4663 19.7778 11.7562 19.802 11.9187C19.8598 12.3072 20.0411 12.6414 20.2194 12.9873L22 16.4407L20.6006 17.1402C20.195 17.3429 19.9923 17.4443 19.851 17.6314C19.7097 17.8184 19.67 18.0296 19.5904 18.4519L19.5826 18.4931C19.4004 19.4606 19.1993 20.5286 18.6329 21.2024C18.4329 21.4403 18.1853 21.6336 17.9059 21.7699C17.4447 21.9948 16.8777 21.9948 15.7437 21.9948C15.219 21.9948 14.6928 22.0069 14.1682 21.9942C12.9247 21.9639 12 20.9184 12 19.7044"/><path d="M14.388 10.5315C13.9617 10.5315 13.5729 10.3702 13.2784 10.1048M14.388 10.5315C14.388 11.6774 13.7241 12.7658 12.4461 12.7658C11.1681 12.7658 10.5043 13.8541 10.5043 15M14.388 10.5315C16.5373 10.5315 16.5373 7.18017 14.388 7.18017C14.1927 7.18017 14.0053 7.21403 13.8312 7.27624C13.9362 4.77819 10.3349 4.1 9.51923 6.44018M10.5043 8.29729C10.5043 7.52323 10.1133 6.8411 9.51923 6.44018M9.51923 6.44018C7.66742 5.19034 5.19883 7.4331 6.37324 9.43277C4.40226 9.72827 4.61299 12.7658 6.6205 12.7658C7.18344 12.7658 7.68111 12.4844 7.98234 12.0538"/></svg>
          Memory
        </button>
        <button class="nav-item" data-view="reviews">
          <svg viewBox="0 0 24 24"><path d="M13.498 2H8.49805C7.66962 2 6.99805 2.67157 6.99805 3.5C6.99805 4.32843 7.66962 5 8.49805 5H13.498C14.3265 5 14.998 4.32843 14.998 3.5C14.998 2.67157 14.3265 2 13.498 2Z"/><path d="M6.99805 15H10.4266M6.99805 11H14.998"/><path d="M18.9981 13.5V9.48263C18.9981 6.65424 18.9981 5.24004 18.1194 4.36137C17.4781 3.72007 16.5515 3.54681 14.9981 3.5M11.998 21.9995L8.99805 21.9995C6.16963 21.9995 4.75541 21.9995 3.87674 21.1208C2.99806 20.2421 2.99805 18.8279 2.99805 15.9995L2.99806 9.48269C2.99805 6.65425 2.99805 5.24004 3.87673 4.36136C4.51802 3.72007 5.44456 3.54681 6.99795 3.5"/><path d="M13.998 20C13.998 20 14.998 20 15.998 22C15.998 22 18.1745 17 20.998 16"/></svg>
          Review queue
          <span class="nav-badge" id="navReviews"></span>
        </button>
        <button class="nav-item" data-view="runs">
          <svg viewBox="0 0 24 24"><path d="M4.31802 19.682C3 18.364 3 16.2426 3 12C3 7.75736 3 5.63604 4.31802 4.31802C5.63604 3 7.75736 3 12 3C16.2426 3 18.364 3 19.682 4.31802C21 5.63604 21 7.75736 21 12C21 16.2426 21 18.364 19.682 19.682C18.364 21 16.2426 21 12 21C7.75736 21 5.63604 21 4.31802 19.682Z"/><path d="M7 14L9.79289 11.2071C10.1834 10.8166 10.8166 10.8166 11.2071 11.2071L12.7929 12.7929C13.1834 13.1834 13.8166 13.1834 14.2071 12.7929L17 10"/></svg>
          Runs
        </button>
        <button class="nav-item" data-view="approvals">
          <svg viewBox="0 0 24 24"><path d="M18.9905 19H19M18.9905 19C18.3678 19.6175 17.2393 19.4637 16.4479 19.4637C15.4765 19.4637 15.0087 19.6537 14.3154 20.347C13.7251 20.9374 12.9337 22 12 22C11.0663 22 10.2749 20.9374 9.68457 20.347C8.99128 19.6537 8.52349 19.4637 7.55206 19.4637C6.76068 19.4637 5.63218 19.6175 5.00949 19C4.38181 18.3776 4.53628 17.2444 4.53628 16.4479C4.53628 15.4414 4.31616 14.9786 3.59938 14.2618C2.53314 13.1956 2.00002 12.6624 2 12C2.00001 11.3375 2.53312 10.8044 3.59935 9.73817C4.2392 9.09832 4.53628 8.46428 4.53628 7.55206C4.53628 6.76065 4.38249 5.63214 5 5.00944C5.62243 4.38178 6.7556 4.53626 7.55208 4.53626C8.46427 4.53626 9.09832 4.2392 9.73815 3.59937C10.8044 2.53312 11.3375 2 12 2C12.6625 2 13.1956 2.53312 14.2618 3.59937C14.9015 4.23907 15.5355 4.53626 16.4479 4.53626C17.2393 4.53626 18.3679 4.38247 18.9906 5C19.6182 5.62243 19.4637 6.75559 19.4637 7.55206C19.4637 8.55858 19.6839 9.02137 20.4006 9.73817C21.4669 10.8044 22 11.3375 22 12C22 12.6624 21.4669 13.1956 20.4006 14.2618C19.6838 14.9786 19.4637 15.4414 19.4637 16.4479C19.4637 17.2444 19.6182 18.3776 18.9905 19Z"/><path d="M9 12.8929C9 12.8929 10.2 13.5447 10.8 14.5C10.8 14.5 12.6 10.75 15 9.5"/></svg>
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
            ${recentRuns.length ? recentRuns.map(runRow).join("") : emptyState("No runs yet", "Send a message through the gateway to start one.")}
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
              </div>`).join("") : emptyState("No sessions", "")}
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
      $("#memInactive").addEventListener("change", () => {
        state.includeInactive = $("#memInactive").checked;
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
        view.innerHTML = emptyState("Syncing", "Talking to the gateway…");
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
