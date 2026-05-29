/**
 * Plex Compress Web UI — Single-page application
 * No build step. Vanilla JS. Chart.js from CDN.
 * All DOM built via helper h() — no innerHTML assignment.
 */

const API = "/api";
const REFRESH_MS = 5000;

/* ---------- Utils ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const fmtBytes = (b) => {
  if (!b || b === 0) return "0 B";
  const k = 1024, s = ["B","KB","MB","GB","TB"];
  const i = Math.floor(Math.log(b) / Math.log(k));
  return parseFloat((b / k ** i).toFixed(1)) + " " + s[i];
};
const fmtBytesDiff = (b) => {
  const gb = b / (1024**3), mb = b / (1024**2);
  if (Math.abs(gb) >= 1) return gb.toFixed(2) + " GB";
  if (Math.abs(mb) >= 1) return Math.round(mb) + " MB";
  return b + " B";
};
const basename = (p) => p ? p.split("/").pop() : "—";
const showName = (p) => {
  const parts = p.split("/");
  return parts.length >= 2 ? parts[parts.length - 2] : basename(p);
};
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/** Build DOM element without innerHTML. */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") { el.textContent = v; }
    else if (k === "className") { el.className = v; }
    else { el.setAttribute(k, v); }
  }
  children.flat().forEach((c) => {
    if (c == null) return;
    if (typeof c === "string" || typeof c === "number") {
      el.appendChild(document.createTextNode(String(c)));
    } else if (c instanceof Node) {
      el.appendChild(c);
    }
  });
  return el;
}

/** Clear element by removing all children. */
function empty(el) { while (el.firstChild) el.removeChild(el.firstChild); }

/* ---------- State ---------- */
let currentView = "dashboard";
const charts = {};
let eventSource = null;
let statusTimer = null;
let configCache = {};

/* ---------- Navigation ---------- */
function showView(name) {
  currentView = name;
  $$(".view-section").forEach((el) => el.classList.remove("active"));
  $(`#view-${name}`)?.classList.add("active");
  $$(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.view === name));
  $(".sidebar")?.classList.remove("open");
  if (name === "reports") loadReports();
  if (name === "queue") loadQueue();
  if (name === "library") loadLibrary();
  if (name === "logs") loadLogs();
  if (name === "extensions") loadExtensions();
  if (name === "config") loadConfigForm();
}

$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});
$(".mobile-toggle")?.addEventListener("click", () => {
  $(".sidebar").classList.toggle("open");
});

/* ---------- API helpers ---------- */
async function apiGet(path) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
async function apiPost(path, body = {}) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

/* ---------- Toasts ---------- */
function toast(message, type = "ok") {
  const box = $(".toast-container");
  const el = h("div", { className: `toast ${type}` }, message);
  box.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

/* ---------- Dashboard ---------- */
async function loadStatus() {
  try {
    const data = await apiGet("/status");
    renderDashboard(data);
  } catch (e) {
    console.error("status fetch failed", e);
  }
}

function renderDashboard(data) {
  const s = data.stats || {};
  const r = data.runner || {};
  const total = s.total || 0;
  const completed = s.completed || 0;
  const failed = s.failed || 0;
  const inProgress = s.in_progress || 0;
  const pending = total - completed - failed - inProgress;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  $("#statCompleted").textContent = completed;
  $("#statFailed").textContent = failed;
  $("#statPending").textContent = pending;
  $("#statSaved").textContent = fmtBytesDiff(s.saved_bytes || 0);
  $("#statSavedSub").textContent = total > 0 ? `${pct}% processed` : "";

  const sess = data.session || {};
  $("#statSessionFiles").textContent = sess.files || 0;
  $("#statSessionSaved").textContent = fmtBytesDiff(sess.saved_bytes || 0);

  const running = r.has_job;
  const pill = $("#statusPill");
  const dot = $("#statusDot");
  if (running) {
    pill.textContent = (r.state || "running").replace(/_/g, " ");
    pill.className = "status-pill running";
    dot.className = "dot";
  } else if (failed > 0 && completed === 0 && total > 0) {
    pill.textContent = "error";
    pill.className = "status-pill error";
    dot.className = "dot err";
  } else {
    pill.textContent = "idle";
    pill.className = "status-pill idle";
    dot.className = "dot idle";
  }

  $("#progressBar").style.width = `${pct}%`;
  $("#progressText").textContent = `${pct}%`;
  $("#legendCompleted").textContent = `${completed} done`;
  $("#legendFailed").textContent = `${failed} failed`;
  $("#legendPending").textContent = `${pending + inProgress} remaining`;

  const runningFile = data.currently_running || (r.progress && r.progress.current_file ? { path: r.progress.current_file } : null);
  if (runningFile) {
    $("#currentFile").textContent = basename(runningFile.path);
    $("#currentShow").textContent = showName(runningFile.path);
  } else if (r.state && r.state !== "idle") {
    $("#currentFile").textContent = r.progress?.message || r.state;
    $("#currentShow").textContent = "";
  } else {
    $("#currentFile").textContent = "Idle — waiting for next job";
    $("#currentShow").textContent = "";
  }

  const recentBody = $("#recentTable");
  empty(recentBody);
  (data.recent || []).slice(0, 20).forEach((f) => {
    const saved = (f.original_size || 0) - (f.output_size || 0);
    const tr = h("tr", {},
      h("td", { title: f.path }, basename(f.path)),
      h("td", { className: `status ${f.status}` }, f.status),
      h("td", { className: "size" }, fmtBytes(f.original_size)),
      h("td", { className: "size" }, fmtBytes(f.output_size)),
      h("td", { className: "saved" }, saved > 0 ? "-" + fmtBytes(saved) : "—")
    );
    recentBody.appendChild(tr);
  });

  const failedBody = $("#failedTable");
  empty(failedBody);
  const fails = (data.failed || []).slice(0, 20);
  if (fails.length === 0) {
    failedBody.appendChild(h("tr", {},
      h("td", { colspan: "2", style: "color:var(--fg2)" }, "No failures — all good!")
    ));
  }
  fails.forEach((f) => {
    failedBody.appendChild(h("tr", {},
      h("td", { title: f.path }, basename(f.path)),
      h("td", { style: "color:var(--err);font-size:0.8rem", title: f.reason || "" }, (f.reason || "").substring(0, 80))
    ));
  });
  if ((data.failed || []).length > 20) {
    failedBody.appendChild(h("tr", {},
      h("td", { colspan: "2", className: "text-muted text-sm" }, `+ ${data.failed.length - 20} more failed files`)
    ));
  }

  const showsGrid = $("#showsGrid");
  empty(showsGrid);
  (data.shows || []).forEach((sItem) => {
    const pctShow = sItem.total > 0 ? Math.round((sItem.completed / sItem.total) * 100) : 0;
    showsGrid.appendChild(h("div", { className: "show-item" },
      h("div", { className: "show-name", title: sItem.name }, sItem.name),
      h("div", { className: "show-meta" }, `${sItem.completed}/${sItem.total} done · ${fmtBytesDiff(sItem.saved)} saved`),
      h("div", { className: "show-bar-wrap" },
        h("div", { className: "show-bar", style: `width:${pctShow}%` })
      )
    ));
  });

  $("#subtitle").textContent = `${total} files tracked across ${(data.shows || []).length} libraries/shows`;
}

/* ---------- Queue ---------- */
async function loadQueue() {
  try {
    const data = await apiGet("/queue");
    const tbody = $("#queueTable");
    empty(tbody);
    (data.queue || []).forEach((f) => {
      tbody.appendChild(h("tr", {},
        h("td", { title: f.path }, basename(f.path)),
        h("td", {}, f.video_codec || "?"),
        h("td", {}, `${f.video_width || "?"}×${f.video_height || "?"}`),
        h("td", { className: "size" }, fmtBytes(f.original_size)),
        h("td", { className: "saved" }, `-${fmtBytes(f.predicted_savings_bytes || 0)}`)
      ));
    });
    if ((data.queue || []).length === 0) {
      tbody.appendChild(h("tr", {}, h("td", { colspan: "5", className: "text-muted" }, "Queue is empty. Run a scan to populate.")));
    }
  } catch (e) {
    toast("Failed to load queue", "err");
  }
}

/* ---------- Library ---------- */
let libraryData = [];
async function loadLibrary() {
  try {
    const [recent, failed, report] = await Promise.all([
      apiGet("/recent"),
      apiGet("/failed"),
      apiGet("/report"),
    ]);
    libraryData = [];
    (report.top_pending || []).forEach((f) => libraryData.push({ ...f, status: "pending" }));
    (recent.recent || []).forEach((f) => libraryData.push({ ...f, status: "completed" }));
    (failed.failed || []).forEach((f) => libraryData.push({ ...f, status: "failed" }));
    renderLibrary();
  } catch (e) {
    toast("Failed to load library", "err");
  }
}
function renderLibrary() {
  const filter = ($("#libFilter").value || "").toLowerCase();
  const statusFilter = $("#libStatusFilter").value;
  const tbody = $("#libraryTable");
  empty(tbody);
  let rows = libraryData.filter((f) => {
    const matches = !filter || (f.path || "").toLowerCase().includes(filter);
    const matchesStatus = !statusFilter || f.status === statusFilter;
    return matches && matchesStatus;
  });
  rows = rows.slice(0, 200);
  rows.forEach((f) => {
    tbody.appendChild(h("tr", {},
      h("td", { title: f.path }, basename(f.path)),
      h("td", { className: `status ${f.status}` }, f.status),
      h("td", {}, f.video_codec || "?"),
      h("td", {}, `${f.video_width || "?"}×${f.video_height || "?"}`),
      h("td", { className: "size" }, fmtBytes(f.original_size)),
      h("td", { className: "size" }, fmtBytes(f.output_size))
    ));
  });
  if (rows.length === 0) {
    tbody.appendChild(h("tr", {}, h("td", { colspan: "6", className: "text-muted" }, "No matching files.")));
  }
}
$("#libFilter")?.addEventListener("input", debounce(renderLibrary, 200));
$("#libStatusFilter")?.addEventListener("change", renderLibrary);

/* ---------- Reports ---------- */
async function loadReports() {
  try {
    const data = await apiGet("/report");
    renderReportCharts(data);
    renderReportTables(data);
  } catch (e) {
    toast("Failed to load reports", "err");
  }
}

function renderReportCharts(report) {
  const byCodec = report.by_codec || [];
  const byRes = report.by_resolution || [];

  const ctxCodec = $("#chartCodec")?.getContext("2d");
  if (ctxCodec) {
    if (charts.codec) charts.codec.destroy();
    charts.codec = new Chart(ctxCodec, {
      type: "doughnut",
      data: {
        labels: byCodec.map((r) => r.video_codec || "unknown"),
        datasets: [{
          data: byCodec.map((r) => Math.round((r.saved || 0) / (1024 ** 3))),
          backgroundColor: ["#4ade80","#60a5fa","#fbbf24","#f87171","#a78bfa","#34d399"],
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "right", labels: { color: "#9aa0ac" } }, title: { display: false } },
      },
    });
  }

  const ctxRes = $("#chartResolution")?.getContext("2d");
  if (ctxRes) {
    if (charts.resolution) charts.resolution.destroy();
    charts.resolution = new Chart(ctxRes, {
      type: "bar",
      data: {
        labels: byRes.map((r) => `${r.video_width || "?"}×${r.video_height || "?"}`),
        datasets: [{
          label: "Saved (GB)",
          data: byRes.map((r) => Math.round((r.saved || 0) / (1024 ** 3) * 10) / 10),
          backgroundColor: "#4ade80",
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: "#9aa0ac" }, grid: { color: "#252b36" } },
          y: { ticks: { color: "#9aa0ac" }, grid: { color: "#252b36" } },
        },
        plugins: { legend: { display: false } },
      },
    });
  }
}

function renderReportTables(report) {
  const tbody = $("#topPendingTable");
  empty(tbody);
  (report.top_pending || []).forEach((f, i) => {
    tbody.appendChild(h("tr", {},
      h("td", {}, i + 1),
      h("td", { title: f.path }, basename(f.path)),
      h("td", {}, f.video_codec || "?"),
      h("td", {}, `${f.video_width || "?"}×${f.video_height || "?"}`),
      h("td", { className: "saved" }, `-${fmtBytes(f.predicted_savings_bytes || 0)}`)
    ));
  });
  if ((report.top_pending || []).length === 0) {
    tbody.appendChild(h("tr", {}, h("td", { colspan: "5", className: "text-muted" }, "No pending candidates.")));
  }

  const histBody = $("#scanHistoryTable");
  empty(histBody);
  (report.scan_history || []).forEach((s) => {
    histBody.appendChild(h("tr", {},
      h("td", {}, s.scanned_at || "?"),
      h("td", {}, s.total_files || 0),
      h("td", {}, s.candidates || 0),
      h("td", {}, `${(s.estimated_savings_gb || 0).toFixed(1)} GB`)
    ));
  });
  if ((report.scan_history || []).length === 0) {
    histBody.appendChild(h("tr", {}, h("td", { colspan: "4", className: "text-muted" }, "No scan history yet.")));
  }

  const summary = report.summary || {};
  const pred = summary.predicted_savings_bytes || 0;
  const err = summary.prediction_error_bytes || 0;
  $("#reportAccuracy").textContent = pred > 0 ? `${((1 - Math.abs(err) / pred) * 100).toFixed(1)}%` : "N/A";
}

/* ---------- Config ---------- */
async function loadConfigForm() {
  try {
    const cfg = await apiGet("/config");
    configCache = cfg;
    $("#cfgLibraryPath").value = cfg.library_path || "";
    $("#cfgTempDir").value = cfg.temp_dir || "";
    $("#cfgStateDb").value = cfg.state_db_path || "";
    $("#cfgLogPath").value = cfg.log_path || "";
    $("#cfgOutputDir").value = cfg.output_dir || "";
    $("#cfgSingleFile").value = cfg.single_file || "";
    $("#cfgIncludePattern").value = cfg.include_pattern || "";
    $("#cfgExclusions").value = (cfg.exclusions || []).join(", ");
    $("#cfgEncoder").value = cfg.video_encoder || "libx265";
    $("#cfgQuality").value = cfg.video_quality ?? 28;
    $("#cfgPreset").value = cfg.video_preset || "medium";
    $("#cfgAudioBitrate").value = cfg.audio_bitrate || "160k";
    $("#cfgParallel").value = cfg.parallel_jobs ?? 1;
    $("#cfgLimit").value = cfg.limit || "";
    $("#cfgWatchInterval").value = cfg.watch_interval ?? 60;
    $("#cfgMinFileAge").value = cfg.min_file_age_seconds ?? 300;
    $("#cfgBackup").checked = !!cfg.keep_backup;
    $("#cfgDryRun").checked = !!cfg.dry_run;
    $("#cfgForce").checked = !!cfg.force;
    $("#cfgVerbose").checked = !!cfg.verbose;
    $("#cfgVerifyChecksum").checked = cfg.verify_checksum !== false;
    $("#cfgFileLocking").checked = cfg.enable_file_locking !== false;
    $("#cfgPostReplaceVerify").checked = cfg.post_replace_verify !== false;
    $("#cfgIntelligentScan").checked = cfg.intelligent_scan !== false;
  } catch (e) {
    toast("Failed to load config", "err");
  }
}

async function saveConfig() {
  const exclusions = $("#cfgExclusions").value.split(",").map((s) => s.trim()).filter(Boolean);
  const payload = {
    library_path: $("#cfgLibraryPath").value || "",
    temp_dir: $("#cfgTempDir").value || "",
    state_db_path: $("#cfgStateDb").value || "",
    log_path: $("#cfgLogPath").value || "",
    output_dir: $("#cfgOutputDir").value || null,
    single_file: $("#cfgSingleFile").value || null,
    include_pattern: $("#cfgIncludePattern").value || null,
    exclusions,
    video_encoder: $("#cfgEncoder").value,
    video_quality: parseInt($("#cfgQuality").value, 10),
    video_preset: $("#cfgPreset").value,
    audio_bitrate: $("#cfgAudioBitrate").value,
    parallel_jobs: parseInt($("#cfgParallel").value, 10),
    limit: $("#cfgLimit").value ? parseInt($("#cfgLimit").value, 10) : null,
    watch_interval: parseFloat($("#cfgWatchInterval").value),
    min_file_age_seconds: parseFloat($("#cfgMinFileAge").value),
    keep_backup: $("#cfgBackup").checked,
    dry_run: $("#cfgDryRun").checked,
    force: $("#cfgForce").checked,
    verbose: $("#cfgVerbose").checked,
    verify_checksum: $("#cfgVerifyChecksum").checked,
    enable_file_locking: $("#cfgFileLocking").checked,
    post_replace_verify: $("#cfgPostReplaceVerify").checked,
    intelligent_scan: $("#cfgIntelligentScan").checked,
  };
  try {
    await apiPost("/config", payload);
    toast("Configuration saved", "ok");
  } catch (e) {
    toast("Save failed: " + e.message, "err");
  }
}

$("#btnSaveConfig")?.addEventListener("click", saveConfig);

function applyPreset(encoder, quality, preset) {
  $("#cfgEncoder").value = encoder;
  $("#cfgQuality").value = quality;
  $("#cfgPreset").value = preset;
  toast(`Preset applied: ${encoder}`, "ok");
}
$("#presetNvenc")?.addEventListener("click", () => applyPreset("hevc_nvenc", 28, "p4"));
$("#presetVideotoolbox")?.addEventListener("click", () => applyPreset("hevc_videotoolbox", 65, "medium"));
$("#presetCpu")?.addEventListener("click", () => applyPreset("libx265", 28, "medium"));

/* ---------- Logs ---------- */
async function loadLogs() {
  try {
    const data = await apiGet("/logs?limit=200");
    renderLogs(data.logs || []);
  } catch (e) {
    toast("Failed to load logs", "err");
  }
}
function renderLogs(lines) {
  const term = $("#logTerminal");
  empty(term);
  lines.forEach((l) => {
    const cls = l.level?.toLowerCase() || "info";
    const text = l.message || l.raw || "";
    term.appendChild(h("div", { className: `log-line ${cls}` },
      h("span", { className: "text-muted" }, l.time || ""),
      " " + text
    ));
  });
  term.scrollTop = term.scrollHeight;
}

/* ---------- Extensions ---------- */
async function loadExtensions() {
  try {
    const data = await apiGet("/extensions");
    const list = $("#extensionsList");
    empty(list);
    (data.extensions || []).forEach((ext) => {
      list.appendChild(h("div", { className: "toggle-row" },
        h("div", {},
          h("div", { className: "toggle-label" }, ext.name),
          h("div", { className: "toggle-hint" }, ext.loaded ? "Loaded" : `Error: ${ext.error || ""}`)
        ),
        h("span", { className: `status-pill ${ext.loaded ? "running" : "error"}` }, ext.loaded ? "Active" : "Failed")
      ));
    });
    if ((data.extensions || []).length === 0) {
      list.appendChild(h("div", { className: "text-muted" }, "No extensions loaded. Place .py files in ~/.plex_compress/webui/extensions/"));
    }
  } catch (e) {
    toast("Failed to load extensions", "err");
  }
}

/* ---------- Actions ---------- */
async function action(path, body = {}, confirmMsg = null) {
  if (confirmMsg && !window.confirm(confirmMsg)) return;
  try {
    const data = await apiPost(path, body);
    toast(data.message || "OK", data.ok ? "ok" : "warn");
    setTimeout(loadStatus, 500);
  } catch (e) {
    toast(e.message, "err");
  }
}

$("#btnHealthCheck")?.addEventListener("click", () => action("/health-check", {}, "Run health check?"));
$("#btnDryRun")?.addEventListener("click", () => action("/scan", { intelligent: true, force: false }, "Run intelligent dry-run scan?"));
$("#btnScan")?.addEventListener("click", () => action("/scan", { intelligent: true, force: false }, "Run intelligent scan?"));
$("#btnTranscode")?.addEventListener("click", () => {
  const limit = parseInt($("#quickLimit").value, 10) || null;
  action("/transcode", { limit, force: false }, "Start batch transcode?");
});
$("#btnWatchStart")?.addEventListener("click", () => action("/watch", { action: "start" }, "Start watch mode?"));
$("#btnWatchStop")?.addEventListener("click", () => action("/watch", { action: "stop" }));
$("#btnStop")?.addEventListener("click", () => action("/stop", {}, "Stop the current operation?"));
$("#btnResetFailed")?.addEventListener("click", () => action("/reset-failed", {}, "Reset all failed entries to pending?"));

/* ---------- SSE ---------- */
function connectEvents() {
  if (eventSource) { try { eventSource.close(); } catch (e) {} }
  eventSource = new EventSource(`${API}/events`);
  eventSource.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "progress") {
        const r = $("#statusPill");
        r.textContent = msg.data.type || "running";
        r.className = "status-pill running";
        $("#statusDot").className = "dot";
        if (msg.data.current_file) {
          $("#currentFile").textContent = basename(msg.data.current_file);
          $("#currentShow").textContent = showName(msg.data.current_file);
        } else {
          $("#currentFile").textContent = msg.data.message || "Working...";
        }
      } else if (msg.type === "finished") {
        toast(msg.data.message, msg.data.ok ? "ok" : "err");
        loadStatus();
      }
    } catch (e) {
      console.warn("SSE parse error", e);
    }
  };
  eventSource.onerror = () => {};
}

/* ---------- Init ---------- */
function init() {
  showView("dashboard");
  loadStatus();
  statusTimer = setInterval(loadStatus, REFRESH_MS);
  connectEvents();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
