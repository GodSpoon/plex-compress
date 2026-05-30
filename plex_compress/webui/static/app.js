/**
 * Plex Compress Web UI — Single-page application
 * Design System v2: Three-layer tokens, component specs, micro-interactions
 * No build step. Vanilla JS. Chart.js from CDN.
 */

const API = "/api";
const REFRESH_MS = 5000;

/* ---------- Utils ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const fmtBytes = (b) => {
	if (!b || b === 0) return "0 B";
	const k = 1024,
		s = ["B", "KB", "MB", "GB", "TB"];
	const i = Math.floor(Math.log(b) / Math.log(k));
	return parseFloat((b / k ** i).toFixed(1)) + " " + s[i];
};
const fmtBytesDiff = (b) => {
	const gb = b / 1024 ** 3,
		mb = b / 1024 ** 2;
	if (Math.abs(gb) >= 1) return gb.toFixed(2) + " GB";
	if (Math.abs(mb) >= 1) return Math.round(mb) + " MB";
	return b + " B";
};
const basename = (p) => (p ? p.split("/").pop() : "—");
const showName = (p) => {
	if (!p) return "—";
	const parts = p.split("/");
	return parts.length >= 2 ? parts[parts.length - 2] : basename(p);
};
const debounce = (fn, ms) => {
	let t;
	return (...a) => {
		clearTimeout(t);
		t = setTimeout(() => fn(...a), ms);
	};
};
const clamp = (n, min, max) => Math.max(min, Math.min(max, n));

/** Build DOM element without innerHTML. */
function h(tag, attrs = {}, ...children) {
	const el = document.createElement(tag);
	for (const [k, v] of Object.entries(attrs)) {
		if (k === "text") {
			el.textContent = v;
		} else if (k === "className") {
			el.className = v;
		} else if (k.startsWith("on") && typeof v === "function") {
			el.addEventListener(k.slice(2).toLowerCase(), v);
		} else {
			el.setAttribute(k, v);
		}
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
function empty(el) {
	while (el.firstChild) el.removeChild(el.firstChild);
}

/* ---------- State ---------- */
let currentView = "dashboard";
const charts = {};
let eventSource = null;
let statusTimer = null;
let configCache = {};
let hasLoadedOnce = false;
let scanReportCache = null;

/* ---------- Command Palette ---------- */
const CMD_ITEMS = [
	{
		label: "Go to Dashboard",
		action: () => showView("dashboard"),
		group: "Navigation",
		shortcut: "g d",
	},
	{
		label: "Go to Queue",
		action: () => showView("queue"),
		group: "Navigation",
		shortcut: "g q",
	},
	{
		label: "Go to Library",
		action: () => showView("library"),
		group: "Navigation",
		shortcut: "g l",
	},
	{
		label: "Go to Reports",
		action: () => showView("reports"),
		group: "Navigation",
		shortcut: "g r",
	},
	{
		label: "Go to Config",
		action: () => showView("config"),
		group: "Navigation",
		shortcut: "g c",
	},
	{
		label: "Go to Logs",
		action: () => showView("logs"),
		group: "Navigation",
		shortcut: "g o",
	},
	{
		label: "Run Health Check",
		action: () => action("/health-check", {}, "Run health check?"),
		group: "Actions",
		shortcut: "h",
	},
	{
		label: "Dry Run Scan",
		action: () =>
			action(
				"/scan",
				{ intelligent: true, force: false },
				"Run intelligent dry-run scan?",
			),
		group: "Actions",
		shortcut: "d",
	},
	{
		label: "Start Transcode",
		action: () =>
			action(
				"/transcode",
				{ limit: parseInt($("#quickLimit")?.value, 10) || null, force: false },
				"Start batch transcode?",
			),
		group: "Actions",
		shortcut: "t",
	},
	{
		label: "Start Watch Mode",
		action: () => action("/watch", { action: "start" }, "Start watch mode?"),
		group: "Actions",
	},
	{
		label: "Stop Watch Mode",
		action: () => action("/watch", { action: "stop" }),
		group: "Actions",
	},
	{
		label: "Stop Current Job",
		action: () => action("/stop", {}, "Stop the current operation?"),
		group: "Actions",
		shortcut: "s",
	},
	{
		label: "Reset Failed",
		action: () =>
			action("/reset-failed", {}, "Reset all failed entries to pending?"),
		group: "Actions",
	},
	{
		label: "Refresh Data",
		action: () => {
			loadStatus();
			toast("Refreshed", "ok");
		},
		group: "Actions",
		shortcut: "r",
	},
];

let cmdSelectedIndex = 0;
let cmdFiltered = [];

function openCmdPalette() {
	$("#cmdOverlay").classList.add("open");
	$("#cmdInput").value = "";
	$("#cmdInput").focus();
	cmdSelectedIndex = 0;
	renderCmdResults("");
}

function closeCmdPalette() {
	$("#cmdOverlay").classList.remove("open");
}

function renderCmdResults(query) {
	const q = query.toLowerCase().trim();
	cmdFiltered = q
		? CMD_ITEMS.filter(
				(i) =>
					i.label.toLowerCase().includes(q) ||
					(i.shortcut && i.shortcut.includes(q)),
			)
		: CMD_ITEMS;

	const container = $("#cmdResults");
	empty(container);

	const groups = {};
	cmdFiltered.forEach((i) => {
		groups[i.group] = groups[i.group] || [];
		groups[i.group].push(i);
	});

	let globalIdx = 0;
	Object.entries(groups).forEach(([group, items]) => {
		container.appendChild(h("div", { className: "cmd-group-label" }, group));
		items.forEach((item) => {
			const idx = globalIdx++;
			const el = h(
				"div",
				{
					className: "cmd-item" + (idx === cmdSelectedIndex ? " selected" : ""),
					onclick: () => {
						closeCmdPalette();
						item.action();
					},
				},
				h("span", {}, item.label),
				item.shortcut ? h("kbd", {}, item.shortcut) : null,
			);
			container.appendChild(el);
		});
	});
}

$("#cmdOverlay")?.addEventListener("click", (e) => {
	if (e.target === $("#cmdOverlay")) closeCmdPalette();
});
$("#cmdInput")?.addEventListener("input", (e) => {
	cmdSelectedIndex = 0;
	renderCmdResults(e.target.value);
});
$("#cmdInput")?.addEventListener("keydown", (e) => {
	if (e.key === "Escape") {
		closeCmdPalette();
		return;
	}
	if (e.key === "Enter") {
		const item = cmdFiltered[cmdSelectedIndex];
		if (item) {
			closeCmdPalette();
			item.action();
		}
		return;
	}
	if (e.key === "ArrowDown") {
		cmdSelectedIndex = clamp(cmdSelectedIndex + 1, 0, cmdFiltered.length - 1);
		renderCmdResults($("#cmdInput").value);
		e.preventDefault();
	}
	if (e.key === "ArrowUp") {
		cmdSelectedIndex = clamp(cmdSelectedIndex - 1, 0, cmdFiltered.length - 1);
		renderCmdResults($("#cmdInput").value);
		e.preventDefault();
	}
});

/* ---------- Navigation ---------- */
function showView(name) {
	currentView = name;
	$$(".view-section").forEach((el) => el.classList.remove("active"));
	$(`#view-${name}`)?.classList.add("active");
	$$(".nav-item").forEach((el) =>
		el.classList.toggle("active", el.dataset.view === name),
	);
	$("#sidebar")?.classList.remove("open");
	if (name === "reports") loadReports();
	if (name === "queue") loadQueue();
	if (name === "library") loadLibrary();
	if (name === "logs") loadLogs();
	if (name === "extensions") loadExtensions();
	if (name === "config") loadConfigForm();
	window.scrollTo({ top: 0, behavior: "smooth" });
}

$$(".nav-item").forEach((btn) => {
	btn.addEventListener("click", () => showView(btn.dataset.view));
});
$("#mobileToggle")?.addEventListener("click", () => {
	$("#sidebar").classList.toggle("open");
});

/* ---------- Keyboard Shortcuts ---------- */
document.addEventListener("keydown", (e) => {
	const tag = document.activeElement?.tagName?.toLowerCase();
	const isTyping = tag === "input" || tag === "select" || tag === "textarea";

	// Cmd/Ctrl+K opens command palette
	if ((e.metaKey || e.ctrlKey) && e.key === "k") {
		e.preventDefault();
		openCmdPalette();
		return;
	}

	if (isTyping) return;

	// g + letter for navigation
	if (e.key === "g") {
		const handler = (ev) => {
			const map = {
				d: "dashboard",
				q: "queue",
				l: "library",
				r: "reports",
				c: "config",
				o: "logs",
				e: "extensions",
			};
			if (map[ev.key]) {
				ev.preventDefault();
				showView(map[ev.key]);
			}
			document.removeEventListener("keydown", handler);
		};
		document.addEventListener("keydown", handler, { once: true });
		return;
	}

	if (e.key === "h") {
		e.preventDefault();
		action("/health-check", {}, "Run health check?");
	}
	if (e.key === "d") {
		e.preventDefault();
		action(
			"/scan",
			{ intelligent: true, force: false },
			"Run intelligent dry-run scan?",
		);
	}
	if (e.key === "t") {
		e.preventDefault();
		action(
			"/transcode",
			{ limit: parseInt($("#quickLimit")?.value, 10) || null, force: false },
			"Start batch transcode?",
		);
	}
	if (e.key === "r") {
		e.preventDefault();
		loadStatus();
		toast("Refreshed", "ok");
	}
	if (e.key === "s") {
		e.preventDefault();
		action("/stop", {}, "Stop the current operation?");
	}
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
	const box = $("#toastContainer");
	const iconMap = { ok: "✓", err: "✕", warn: "⚠", info: "ℹ" };
	const el = h(
		"div",
		{ className: `toast ${type}` },
		h(
			"span",
			{ style: "font-weight:bold;min-width:18px;text-align:center" },
			iconMap[type] || "•",
		),
		h("span", { style: "flex:1;line-height:1.5" }, message),
	);
	box.appendChild(el);
	setTimeout(() => {
		el.classList.add("toast-out");
		setTimeout(() => el.remove(), 300);
	}, 4000);
}

/* ---------- Dashboard ---------- */
async function loadStatus() {
	try {
		const [data, scanReport] = await Promise.all([
			apiGet("/status"),
			apiGet("/scan-report").catch(() => null),
		]);
		if (scanReport) scanReportCache = scanReport;
		renderDashboard(data);
		if (!hasLoadedOnce) {
			$("#dashSkeleton").style.display = "none";
			$("#statsGrid").style.display = "grid";
			hasLoadedOnce = true;
		}
	} catch (e) {
		console.error("status fetch failed", e);
		if (!hasLoadedOnce) {
			$("#subtitle").textContent = "Cannot reach API — " + e.message;
		}
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

	$("#statCompleted").textContent = completed.toLocaleString();
	$("#statCompletedSub").textContent = total > 0 ? `${pct}% of library` : "";
	$("#statFailed").textContent = failed.toLocaleString();
	$("#statFailedSub").textContent = failed > 0 ? "needs attention" : "";
	$("#statPending").textContent = pending.toLocaleString();
	$("#statSaved").textContent = fmtBytesDiff(s.saved_bytes || 0);
	$("#statSavedSub").textContent = total > 0 ? `${pct}% processed` : "";

	const sess = data.session || {};
	$("#statSessionFiles").textContent = (sess.files || 0).toLocaleString();
	$("#statSessionFilesSub").textContent = "files this session";
	const sessSaved = sess.saved_bytes || 0;
	const sessOrig = sess.original_size || 1;
	const sessPct = sessOrig > 0 ? Math.round((sessSaved / sessOrig) * 100) : 0;
	$("#statSessionSaved").textContent = fmtBytesDiff(sessSaved);
	$("#statSessionSavedSub").textContent = sessPct + "% compression";

	const running = r.has_job;
	const pill = $("#statusPill");
	const dot = $("#statusDot");
	const activityStatus = $("#activityStatus");

	if (running) {
		pill.textContent = (r.state || "running").replace(/_/g, " ");
		pill.className = "status-pill running";
		dot.className = "brand-dot";
		activityStatus.textContent = "Running";
		activityStatus.className = "status-pill running";
	} else if (failed > 0 && completed === 0 && total > 0) {
		pill.textContent = "error";
		pill.className = "status-pill error";
		dot.className = "brand-dot error";
		activityStatus.textContent = "Error";
		activityStatus.className = "status-pill error";
	} else {
		pill.textContent = "idle";
		pill.className = "status-pill idle";
		dot.className = "brand-dot idle";
		activityStatus.textContent = "Idle";
		activityStatus.className = "status-pill idle";
	}

	$("#progressBar").style.width = `${pct}%`;
	$("#progressText").textContent = `${pct}%`;
	$("#progressMetaTop").textContent = `${completed + failed} of ${total} files`;
	$("#legendCompleted").textContent = `${completed.toLocaleString()} done`;
	$("#legendFailed").textContent = `${failed.toLocaleString()} failed`;
	$("#legendPending").textContent =
		`${(pending + inProgress).toLocaleString()} remaining`;

	const runningFile =
		data.currently_running ||
		(r.progress && r.progress.current_file
			? { path: r.progress.current_file }
			: null);
	const currentFileEl = $("#currentFile");
	if (runningFile) {
		currentFileEl.textContent = basename(runningFile.path);
		currentFileEl.classList.add("pulse");
		$("#currentShow").textContent = showName(runningFile.path);
	} else if (r.state && r.state !== "idle") {
		currentFileEl.textContent = r.progress?.message || r.state;
		currentFileEl.classList.remove("pulse");
		$("#currentShow").textContent = "";
	} else {
		currentFileEl.textContent = "Idle — waiting for next job";
		currentFileEl.classList.remove("pulse");
		$("#currentShow").textContent = "";
	}

	// Recent table
	const recentBody = $("#recentTable");
	empty(recentBody);
	const recent = (data.recent || []).slice(0, 20);
	if (recent.length === 0) {
		recentBody.appendChild(emptyRow("No recent transcodes yet.", 5));
	}
	recent.forEach((f) => {
		const saved = (f.original_size || 0) - (f.output_size || 0);
		recentBody.appendChild(
			h(
				"tr",
				{},
				h("td", { title: f.path }, basename(f.path)),
				h("td", { className: `status ${f.status}` }, f.status),
				h("td", { className: "size" }, fmtBytes(f.original_size)),
				h("td", { className: "size" }, fmtBytes(f.output_size)),
				h(
					"td",
					{ className: "saved" },
					saved > 0 ? "-" + fmtBytes(saved) : "—",
				),
			),
		);
	});

	// Failed table
	const failedBody = $("#failedTable");
	empty(failedBody);
	const fails = (data.failed || []).slice(0, 20);
	if (fails.length === 0) {
		failedBody.appendChild(emptyRow("No failures — all good!", 2));
	}
	fails.forEach((f) => {
		failedBody.appendChild(
			h(
				"tr",
				{},
				h("td", { title: f.path }, basename(f.path)),
				h(
					"td",
					{
						style: "color:var(--destructive);font-size:0.8rem",
						title: f.reason || "",
					},
					(f.reason || "").substring(0, 80),
				),
			),
		);
	});
	if ((data.failed || []).length > 20) {
		failedBody.appendChild(
			h(
				"tr",
				{},
				h(
					"td",
					{ colspan: "2", className: "text-muted text-sm" },
					`+ ${data.failed.length - 20} more failed files`,
				),
			),
		);
	}

	// Shows grid
	const showsGrid = $("#showsGrid");
	empty(showsGrid);
	const shows = data.shows || [];
	if (shows.length === 0) {
		showsGrid.appendChild(
			emptyCard("No shows tracked yet. Run a scan to get started."),
		);
	}
	shows.forEach((sItem) => {
		const pctShow =
			sItem.total > 0 ? Math.round((sItem.completed / sItem.total) * 100) : 0;
		showsGrid.appendChild(
			h(
				"div",
				{ className: "show-item" },
				h("div", { className: "show-name", title: sItem.name }, sItem.name),
				h(
					"div",
					{ className: "show-meta" },
					`${sItem.completed}/${sItem.total} done · ${fmtBytesDiff(sItem.saved)} saved`,
				),
				h(
					"div",
					{ className: "show-bar-track" },
					h("div", { className: "show-bar-fill", style: `width:${pctShow}%` }),
				),
			),
		);
	});

	$("#subtitle").textContent =
		`${total.toLocaleString()} files tracked across ${shows.length} libraries/shows`;

	// Scan-report derived stats
	if (scanReportCache) {
		const ss = scanReportCache.scan_summary || {};
		const vel = scanReportCache.velocity || {};
		const ts = scanReportCache.time_stats || {};
		const eta = scanReportCache.eta || {};

		$("#statProjectedSavings").textContent = fmtBytesDiff(ss.estimated_savings_bytes || 0);
		$("#statProjectedSavingsSub").textContent =
			ss.total_library_size_bytes > 0
				? `${((ss.estimated_savings_bytes / ss.total_library_size_bytes) * 100).toFixed(1)}% of library`
				: "from pending files";

		$("#statVelocity").textContent =
			vel.gb_per_hour != null ? vel.gb_per_hour.toFixed(2) : "—";
		$("#statVelocitySub").textContent =
			vel.files_per_hour != null ? `${vel.files_per_hour.toFixed(1)} files/hr` : "GB per hour";

		$("#statTimeSpent").textContent =
			ts.total_transcode_hours != null ? ts.total_transcode_hours.toFixed(1) : "—";
		$("#statTimeSpentSub").textContent =
			ts.session_count != null ? `${ts.session_count} sessions` : "total transcode hours";

		const days = eta.days_remaining;
		$("#statETA").textContent = days != null ? (days < 1 ? "< 1 day" : `${Math.ceil(days)} days`) : "∞";
		$("#statETASub").textContent =
			days != null ? `${eta.pending_files || 0} files remaining` : "no velocity data";

		// Enhanced progress text
		const actualSaved = s.saved_bytes || 0;
		const projectedMore = ss.estimated_savings_bytes || 0;
		const totalPotential = actualSaved + projectedMore;
		$("#progressSavingsLine").textContent =
			`Actual: ${fmtBytesDiff(actualSaved)} saved · Projected: ${fmtBytesDiff(projectedMore)} more · Total potential: ${fmtBytesDiff(totalPotential)}`;

		// Dashboard charts
		renderDashboardCharts(scanReportCache);
	} else {
		$("#statProjectedSavings").textContent = "—";
		$("#statVelocity").textContent = "—";
		$("#statTimeSpent").textContent = "—";
		$("#statETA").textContent = "—";
		$("#progressSavingsLine").textContent = "Run a scan to see savings projections";
	}
}

/* ---------- Scan Report Modal ---------- */
async function loadScanReport() {
	try {
		const data = await apiGet("/scan-report");
		scanReportCache = data;
		renderScanModal(data);
	} catch (e) {
		toast("Failed to load scan report", "err");
	}
}

function openScanModal() {
	$("#scanReportModal").style.display = "flex";
	if (scanReportCache) {
		renderScanModal(scanReportCache);
	} else {
		loadScanReport();
	}
}

function closeScanModal() {
	$("#scanReportModal").style.display = "none";
}

function renderScanModal(data) {
	const ss = data.scan_summary || {};
	const byShow = data.by_show || [];
	const byCodec = data.by_codec || [];

	// Hero
	$("#scanHeroNumber").textContent = (ss.estimated_savings_gb || 0).toFixed(1);
	const pendingPct = ss.total_library_size_bytes > 0
		? ((ss.pending_size_bytes / ss.total_library_size_bytes) * 100).toFixed(1)
		: 0;
	const totalTB = (ss.total_library_size_bytes || 0) / 1024 ** 4;
	$("#scanHeroSub").textContent = `That's ${pendingPct}% of your ${totalTB.toFixed(2)} TB library`;
	$("#scanHeroSub2").textContent = `${(ss.candidates || 0).toLocaleString()} files remaining out of ${(ss.total_files || 0).toLocaleString()} total`;

	// Mini stats
	$("#scanStatLibrarySize").textContent = fmtBytes(ss.total_library_size_bytes || 0);
	$("#scanStatPendingSize").textContent = fmtBytes(ss.pending_size_bytes || 0);
	$("#scanStatSavedSoFar").textContent = fmtBytes(ss.saved_so_far_bytes || 0);
	$("#scanStatOptimal").textContent = (ss.already_optimal || 0).toLocaleString();

	// Treemap: top 20 shows by pending size
	renderScanTreemap(byShow.slice(0, 20));

	// Top 10 shows horizontal bar
	renderScanTopShows(byShow.slice(0, 10));

	// Codec doughnut
	renderScanCodec(byCodec);

	// Media type stacked bar
	renderScanMediaTypeBar(ss);

	// Codec table legend
	renderScanCodecTable(byCodec);
}

function renderScanTreemap(shows) {
	const ctx = $("#chartScanTreemap")?.getContext("2d");
	if (!ctx) return;
	if (charts.scanTreemap) charts.scanTreemap.destroy();
	if (shows.length === 0) {
		setChartEmpty("#chartScanTreemap", true, "No show data available.");
		return;
	}
	setChartEmpty("#chartScanTreemap", false);

	const treeData = shows.map((s) => ({
		name: s.name || "Unknown",
		value: s.pending_size || 0,
		predicted: s.predicted_savings || 0,
	}));

	const mediaTypeColors = { tv_shows: "#3b82f6", movies: "#10b981", other: "#6b7280" };
	// Infer media type from name heuristic
	function inferMediaType(name) {
		if (!name) return "other";
		if (name === "Movies") return "movies";
		if (/S\d{2}E\d{2}/i.test(name) || name.includes("Season")) return "tv_shows";
		return "tv_shows"; // default most shows to TV
	}

	charts.scanTreemap = new Chart(ctx, {
		type: "treemap",
		data: {
			datasets: [{
				tree: treeData,
				key: "value",
				groups: ["name"],
				spacing: 1.5,
				borderWidth: 1,
				borderColor: "rgba(255,255,255,0.08)",
				borderRadius: 4,
				backgroundColor: (ctx) => {
					const item = ctx.raw;
					if (!item) return "#6b7280";
					const name = item._data?.name || item.g || "";
					const mt = inferMediaType(name);
					return mediaTypeColors[mt] || "#6b7280";
				},
				labels: {
					align: "left",
					color: "#f0f2f5",
					font: { size: 11, weight: "600" },
					formatter: (ctx) => {
						const item = ctx.raw;
						if (!item) return "";
						const name = item._data?.name || item.g || "";
						const val = item.v || 0;
						if (val < 1024 * 1024 * 50) return ""; // skip tiny cells
						return name.length > 18 ? name.slice(0, 16) + "…" : name;
					},
				},
			}],
		},
		options: {
			responsive: true,
			maintainAspectRatio: false,
			plugins: {
				legend: { display: false },
				tooltip: {
					backgroundColor: "#151921",
					borderColor: "rgba(255,255,255,0.09)",
					borderWidth: 1,
					titleColor: "#f0f2f5",
					bodyColor: "#9aa3b2",
					padding: 12,
					cornerRadius: 10,
					callbacks: {
						title: (items) => {
							const item = items[0].raw;
							return item?._data?.name || "";
						},
						label: (item) => {
							const raw = item.raw;
							return [
								`Pending: ${fmtBytes(raw?.v || 0)}`,
								`Predicted save: ${fmtBytes(raw?._data?.predicted || 0)}`,
							];
						},
					},
				},
			},
		},
	});
}

function renderScanTopShows(shows) {
	const ctx = $("#chartScanTopShows")?.getContext("2d");
	if (!ctx) return;
	if (charts.scanTopShows) charts.scanTopShows.destroy();
	if (shows.length === 0) {
		setChartEmpty("#chartScanTopShows", true, "No show data available.");
		return;
	}
	setChartEmpty("#chartScanTopShows", false);

	const mediaTypeColors = { tv_shows: "#3b82f6", movies: "#10b981", other: "#6b7280" };
	function inferMediaType(name) {
		if (!name) return "other";
		if (name === "Movies") return "movies";
		return "tv_shows";
	}

	charts.scanTopShows = new Chart(ctx, {
		type: "bar",
		data: {
			labels: shows.map((s) => (s.name || "Unknown").substring(0, 24)),
			datasets: [{
				label: "Predicted Savings (GB)",
				data: shows.map((s) => Math.round(((s.predicted_savings || 0) / 1024 ** 3) * 10) / 10),
				backgroundColor: shows.map((s) => mediaTypeColors[inferMediaType(s.name)] || "#6b7280"),
				borderRadius: 6,
				borderSkipped: false,
			}],
		},
		options: {
			indexAxis: "y",
			responsive: true,
			maintainAspectRatio: false,
			scales: {
				x: {
					ticks: { color: "#9aa3b2", font: { size: 11 } },
					grid: { color: "rgba(255,255,255,0.04)" },
				},
				y: {
					ticks: { color: "#9aa3b2", font: { size: 11 } },
					grid: { display: false },
				},
			},
			plugins: {
				legend: { display: false },
				tooltip: {
					backgroundColor: "#151921",
					borderColor: "rgba(255,255,255,0.09)",
					borderWidth: 1,
					titleColor: "#f0f2f5",
					bodyColor: "#9aa3b2",
					padding: 12,
					cornerRadius: 10,
				},
			},
		},
	});
}

function renderScanCodec(byCodec) {
	const ctx = $("#chartScanCodec")?.getContext("2d");
	if (!ctx) return;
	if (charts.scanCodec) charts.scanCodec.destroy();
	if (byCodec.length === 0) {
		setChartEmpty("#chartScanCodec", true, "No codec data available.");
		return;
	}
	setChartEmpty("#chartScanCodec", false);

	const palette = ["#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6", "#34d399", "#60a5fa", "#fbbf24"];
	charts.scanCodec = new Chart(ctx, {
		type: "doughnut",
		data: {
			labels: byCodec.map((r) => r.video_codec || "unknown"),
			datasets: [{
				data: byCodec.map((r) => r.count || 0),
				backgroundColor: byCodec.map((_, i) => palette[i % palette.length]),
				borderWidth: 0,
				hoverOffset: 6,
			}],
		},
		options: {
			responsive: true,
			maintainAspectRatio: false,
			cutout: "65%",
			plugins: {
				legend: {
					position: "right",
					labels: { color: "#9aa3b2", font: { size: 12 }, padding: 16 },
				},
				tooltip: {
					backgroundColor: "#151921",
					borderColor: "rgba(255,255,255,0.09)",
					borderWidth: 1,
					titleColor: "#f0f2f5",
					bodyColor: "#9aa3b2",
					padding: 12,
					cornerRadius: 10,
				},
			},
		},
	});
}

function renderScanCodecTable(byCodec) {
	const container = $("#scanCodecTable");
	empty(container);
	if (byCodec.length === 0) return;

	const table = h("table", { className: "scan-codec-table-inner" });
	const thead = h("thead", {},
		h("tr", {},
			h("th", {}, "Codec"),
			h("th", { className: "text-right" }, "Files"),
			h("th", { className: "text-right" }, "Est. Savings"),
		),
	);
	const tbody = h("tbody");
	byCodec.forEach((c) => {
		tbody.appendChild(
			h("tr", {},
				h("td", {}, c.video_codec || "unknown"),
				h("td", { className: "text-right" }, (c.count || 0).toLocaleString()),
				h("td", { className: "text-right saved" }, fmtBytesDiff(c.predicted_savings || 0)),
			),
		);
	});
	table.appendChild(thead);
	table.appendChild(tbody);
	container.appendChild(table);
}

function renderScanMediaTypeBar(ss) {
	const container = $("#scanMediaTypeBar");
	empty(container);

	// Use counts for the stacked bar
	const totalFiles = ss.total_files || 1;
	const completedFiles = ss.total_files - ss.candidates - ss.already_optimal || 0;
	const pendingFiles = ss.candidates || 0;
	const optimalFiles = ss.already_optimal || 0;
	const failedFiles = ss.total_files - completedFiles - pendingFiles - optimalFiles;

	const segments = [
		{ label: "Completed", count: Math.max(0, completedFiles), color: "#10b981" },
		{ label: "Pending", count: Math.max(0, pendingFiles), color: "#3b82f6" },
		{ label: "Already Optimal", count: Math.max(0, optimalFiles), color: "#6b7280" },
		{ label: "Failed", count: Math.max(0, failedFiles), color: "#ef4444" },
	];

	const bar = h("div", { className: "stacked-bar" });
	segments.forEach((seg) => {
		if (seg.count === 0) return;
		const pct = ((seg.count / totalFiles) * 100).toFixed(1);
		bar.appendChild(
			h("div", {
				className: "stacked-bar-seg",
				style: `width:${pct}%;background:${seg.color};`,
				title: `${seg.label}: ${seg.count.toLocaleString()} (${pct}%)`,
			},
				h("span", { className: "stacked-bar-label" }, `${seg.count.toLocaleString()}`),
			),
		);
	});

	const legend = h("div", { className: "stacked-bar-legend" });
	segments.forEach((seg) => {
		if (seg.count === 0) return;
		legend.appendChild(
			h("span", { className: "stacked-bar-legend-item" },
				h("span", { className: "legend-dot", style: `background:${seg.color};` }),
				` ${seg.label} (${seg.count.toLocaleString()})`,
			),
		);
	});

	container.appendChild(bar);
	container.appendChild(legend);
}

/* ---------- Dashboard Charts ---------- */
function renderDashboardCharts(report) {
	const byMediaType = report.by_media_type || {};
	const byShow = (report.by_show || []).slice(0, 5);

	// Media type doughnut
	const ctxMedia = $("#chartDashMediaType")?.getContext("2d");
	if (ctxMedia) {
		if (charts.dashMediaType) charts.dashMediaType.destroy();
		const mtData = [
			byMediaType.tv_shows?.count || 0,
			byMediaType.movies?.count || 0,
			byMediaType.other?.count || 0,
		];
		const hasData = mtData.some((v) => v > 0);
		setChartEmpty("#chartDashMediaType", !hasData, "No media type data.");
		if (hasData) {
			charts.dashMediaType = new Chart(ctxMedia, {
				type: "doughnut",
				data: {
					labels: ["TV Shows", "Movies", "Other"],
					datasets: [{
						data: mtData,
						backgroundColor: ["#3b82f6", "#10b981", "#6b7280"],
						borderWidth: 0,
						hoverOffset: 6,
					}],
				},
				options: {
					responsive: true,
					maintainAspectRatio: false,
					cutout: "65%",
					plugins: {
						legend: {
							position: "bottom",
							labels: { color: "#9aa3b2", font: { size: 12 }, padding: 16 },
						},
						tooltip: {
							backgroundColor: "#151921",
							borderColor: "rgba(255,255,255,0.09)",
							borderWidth: 1,
							titleColor: "#f0f2f5",
							bodyColor: "#9aa3b2",
							padding: 12,
							cornerRadius: 10,
						},
					},
				},
			});
		}
	}

	// Top 5 shows horizontal bar
	const ctxShows = $("#chartDashTopShows")?.getContext("2d");
	if (ctxShows) {
		if (charts.dashTopShows) charts.dashTopShows.destroy();
		if (byShow.length === 0) {
			setChartEmpty("#chartDashTopShows", true, "No show data available.");
		} else {
			setChartEmpty("#chartDashTopShows", false);
			charts.dashTopShows = new Chart(ctxShows, {
				type: "bar",
				data: {
					labels: byShow.map((s) => (s.name || "Unknown").substring(0, 20)),
					datasets: [{
						label: "Predicted Savings (GB)",
						data: byShow.map((s) => Math.round(((s.predicted_savings || 0) / 1024 ** 3) * 10) / 10),
						backgroundColor: "#3b82f6",
						borderRadius: 6,
						borderSkipped: false,
					}],
				},
				options: {
					indexAxis: "y",
					responsive: true,
					maintainAspectRatio: false,
					scales: {
						x: {
							ticks: { color: "#9aa3b2", font: { size: 11 } },
							grid: { color: "rgba(255,255,255,0.04)" },
						},
						y: {
							ticks: { color: "#9aa3b2", font: { size: 11 } },
							grid: { display: false },
						},
					},
					plugins: {
						legend: { display: false },
						tooltip: {
							backgroundColor: "#151921",
							borderColor: "rgba(255,255,255,0.09)",
							borderWidth: 1,
							titleColor: "#f0f2f5",
							bodyColor: "#9aa3b2",
							padding: 12,
							cornerRadius: 10,
						},
					},
				},
			});
		}
	}
}

function emptyRow(message, cols) {
	return h(
		"tr",
		{},
		h(
			"td",
			{
				colspan: String(cols),
				style:
					"text-align:center;padding:var(--sp-8) var(--sp-4);color:var(--fg-tertiary);",
			},
			h(
				"div",
				{ className: "empty-state", style: "padding:0" },
				h("div", { className: "empty-state-icon" }, "📭"),
				h("div", { className: "empty-state-title" }, message),
			),
		),
	);
}

function emptyCard(message) {
	return h(
		"div",
		{ className: "empty-state" },
		h("div", { className: "empty-state-icon" }, "📭"),
		h("div", { className: "empty-state-title" }, message),
	);
}

/* ---------- Queue ---------- */
async function loadQueue() {
	try {
		const data = await apiGet("/queue");
		const tbody = $("#queueTable");
		empty(tbody);
		const queue = data.queue || [];
		if (queue.length === 0) {
			tbody.appendChild(emptyRow("Queue is empty. Run a scan to populate.", 6));
		}
		queue.forEach((f) => {
			tbody.appendChild(
				h(
					"tr",
					{},
					h("td", { title: f.path }, basename(f.path)),
					h("td", {}, f.video_codec || "?"),
					h("td", {}, `${f.video_width || "?"}×${f.video_height || "?"}`),
					h("td", { className: "size" }, fmtBytes(f.original_size)),
					h(
						"td",
						{ className: "saved" },
						`-${fmtBytes(f.predicted_savings_bytes || 0)}`,
					),
					h("td", { className: `status ${f.status || "pending"}` }, f.status || "pending"),
				),
			);
		});
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
		(report.top_pending || []).forEach((f) =>
			libraryData.push({ ...f, status: "pending" }),
		);
		(recent.recent || []).forEach((f) =>
			libraryData.push({ ...f, status: "completed" }),
		);
		(failed.failed || []).forEach((f) =>
			libraryData.push({ ...f, status: "failed" }),
		);
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
	if (rows.length === 0) {
		tbody.appendChild(emptyRow("No matching files.", 6));
	}
	rows.forEach((f) => {
		tbody.appendChild(
			h(
				"tr",
				{},
				h("td", { title: f.path }, basename(f.path)),
				h("td", { className: `status ${f.status}` }, f.status),
				h("td", {}, f.video_codec || "?"),
				h("td", {}, `${f.video_width || "?"}×${f.video_height || "?"}`),
				h("td", { className: "size" }, fmtBytes(f.original_size)),
				h("td", { className: "size" }, fmtBytes(f.output_size)),
			),
		);
	});
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

/** Show or hide empty state overlay inside a chart container. */
function setChartEmpty(containerSelector, isEmpty, message) {
	const container = $(containerSelector)?.parentElement;
	if (!container) return;
	let overlay = container.querySelector(".chart-empty");
	if (isEmpty) {
		if (!overlay) {
			overlay = h(
				"div",
				{
					className: "chart-empty empty-state",
					style: "position:absolute;inset:0;background:transparent;",
				},
				h("div", { className: "empty-state-icon" }, "📊"),
				h("div", { className: "empty-state-title" }, message),
			);
			container.style.position = "relative";
			container.appendChild(overlay);
		}
	} else if (overlay) {
		overlay.remove();
	}
}

function renderReportCharts(report) {
	const byCodec = report.by_codec || [];
	const byRes = report.by_resolution || [];

	// Codec doughnut
	const ctxCodec = $("#chartCodec")?.getContext("2d");
	if (ctxCodec) {
		if (charts.codec) charts.codec.destroy();
		setChartEmpty("#chartCodec", byCodec.length === 0, "No codec data yet.");
		if (byCodec.length === 0) return;
		charts.codec = new Chart(ctxCodec, {
			type: "doughnut",
			data: {
				labels: byCodec.map((r) => r.video_codec || "unknown"),
				datasets: [
					{
						data: byCodec.map((r) => Math.round((r.saved || 0) / 1024 ** 3)),
						backgroundColor: [
							"#10b981",
							"#3b82f6",
							"#f59e0b",
							"#ef4444",
							"#8b5cf6",
							"#34d399",
							"#60a5fa",
							"#fbbf24",
						],
						borderWidth: 0,
						hoverOffset: 6,
					},
				],
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				cutout: "65%",
				plugins: {
					legend: {
						position: "right",
						labels: { color: "#9aa3b2", font: { size: 12 }, padding: 16 },
					},
					tooltip: {
						backgroundColor: "#151921",
						borderColor: "rgba(255,255,255,0.09)",
						borderWidth: 1,
						titleColor: "#f0f2f5",
						bodyColor: "#9aa3b2",
						padding: 12,
						cornerRadius: 10,
					},
				},
			},
		});
	}

	// Resolution bar
	const ctxRes = $("#chartResolution")?.getContext("2d");
	if (ctxRes) {
		if (charts.resolution) charts.resolution.destroy();
		setChartEmpty(
			"#chartResolution",
			byRes.length === 0,
			"No resolution data yet.",
		);
		if (byRes.length === 0) return;
		charts.resolution = new Chart(ctxRes, {
			type: "bar",
			data: {
				labels: byRes.map(
					(r) => `${r.video_width || "?"}×${r.video_height || "?"}`,
				),
				datasets: [
					{
						label: "Saved (GB)",
						data: byRes.map(
							(r) => Math.round(((r.saved || 0) / 1024 ** 3) * 10) / 10,
						),
						backgroundColor: "#10b981",
						borderRadius: 6,
						hoverBackgroundColor: "#34d399",
					},
				],
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				scales: {
					x: {
						ticks: { color: "#9aa3b2", font: { size: 11 } },
						grid: { color: "rgba(255,255,255,0.04)" },
					},
					y: {
						ticks: { color: "#9aa3b2", font: { size: 11 } },
						grid: { color: "rgba(255,255,255,0.04)" },
					},
				},
				plugins: {
					legend: { display: false },
					tooltip: {
						backgroundColor: "#151921",
						borderColor: "rgba(255,255,255,0.09)",
						borderWidth: 1,
						titleColor: "#f0f2f5",
						bodyColor: "#9aa3b2",
						padding: 12,
						cornerRadius: 10,
					},
				},
			},
		});
	}
}

function renderReportTables(report) {
	const tbody = $("#topPendingTable");
	empty(tbody);
	const topPending = report.top_pending || [];
	if (topPending.length === 0) {
		tbody.appendChild(emptyRow("No pending candidates.", 5));
	}
	topPending.forEach((f, i) => {
		tbody.appendChild(
			h(
				"tr",
				{},
				h("td", {}, i + 1),
				h("td", { title: f.path }, basename(f.path)),
				h("td", {}, f.video_codec || "?"),
				h("td", {}, `${f.video_width || "?"}×${f.video_height || "?"}`),
				h(
					"td",
					{ className: "saved" },
					`-${fmtBytes(f.predicted_savings_bytes || 0)}`,
				),
			),
		);
	});

	const histBody = $("#scanHistoryTable");
	empty(histBody);
	const scans = report.scan_history || [];
	if (scans.length === 0) {
		histBody.appendChild(emptyRow("No scan history yet.", 4));
	}
	scans.forEach((s) => {
		histBody.appendChild(
			h(
				"tr",
				{},
				h("td", {}, s.scanned_at || "?"),
				h("td", {}, (s.total_files || 0).toLocaleString()),
				h("td", {}, (s.candidates || 0).toLocaleString()),
				h("td", {}, `${(s.estimated_savings_gb || 0).toFixed(1)} GB`),
			),
		);
	});

	const summary = report.summary || {};
	const pred = summary.predicted_savings_bytes || 0;
	const err = summary.prediction_error_bytes || 0;
	$("#reportAccuracy").textContent =
		pred > 0 ? `${((1 - Math.abs(err) / pred) * 100).toFixed(1)}%` : "N/A";
	$("#reportTotal").textContent = (summary.completed || 0).toLocaleString();
	$("#reportTotalSaved").textContent = fmtBytesDiff(summary.saved_bytes || 0);
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
	const exclusions = $("#cfgExclusions")
		.value.split(",")
		.map((s) => s.trim())
		.filter(Boolean);
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
$("#presetNvenc")?.addEventListener("click", () =>
	applyPreset("hevc_nvenc", 28, "p4"),
);
$("#presetVideotoolbox")?.addEventListener("click", () =>
	applyPreset("hevc_videotoolbox", 65, "medium"),
);
$("#presetCpu")?.addEventListener("click", () =>
	applyPreset("libx265", 28, "medium"),
);

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
	if (lines.length === 0) {
		term.appendChild(emptyCard("No log entries yet."));
		return;
	}
	lines.forEach((l) => {
		const cls = l.level?.toLowerCase() || "info";
		const text = l.message || l.raw || "";
		term.appendChild(
			h(
				"div",
				{ className: `log-line ${cls}` },
				h("span", { className: "text-muted" }, l.time || ""),
				" " + text,
			),
		);
	});
	term.scrollTop = term.scrollHeight;
}

/* ---------- Extensions ---------- */
async function loadExtensions() {
	try {
		const data = await apiGet("/extensions");
		const list = $("#extensionsList");
		empty(list);
		const exts = data.extensions || [];
		if (exts.length === 0) {
			list.appendChild(
				emptyCard(
					"No extensions loaded. Place .py files in ~/.plex_compress/webui/extensions/",
				),
			);
			return;
		}
		exts.forEach((ext) => {
			list.appendChild(
				h(
					"div",
					{ className: "toggle-row" },
					h(
						"div",
						{},
						h("div", { className: "toggle-label" }, ext.name),
						h(
							"div",
							{ className: "toggle-hint" },
							ext.loaded ? "Loaded successfully" : `Error: ${ext.error || ""}`,
						),
					),
					h(
						"span",
						{ className: `status-pill ${ext.loaded ? "running" : "error"}` },
						ext.loaded ? "Active" : "Failed",
					),
				),
			);
		});
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

$("#btnHealthCheck")?.addEventListener("click", () =>
	action("/health-check", {}, "Run health check?"),
);
$("#btnDryRun")?.addEventListener("click", () =>
	action(
		"/scan",
		{ intelligent: true, force: false },
		"Run intelligent dry-run scan?",
	),
);
$("#btnScan")?.addEventListener("click", () =>
	action("/scan", { intelligent: true, force: false }, "Run intelligent scan?"),
);
$("#btnTranscode")?.addEventListener("click", () => {
	const limit = parseInt($("#quickLimit").value, 10) || null;
	action("/transcode", { limit, force: false }, "Start batch transcode?");
});
$("#btnWatchStart")?.addEventListener("click", () =>
	action("/watch", { action: "start" }, "Start watch mode?"),
);
$("#btnWatchStop")?.addEventListener("click", () =>
	action("/watch", { action: "stop" }),
);
$("#btnStop")?.addEventListener("click", () =>
	action("/stop", {}, "Stop the current operation?"),
);
$("#btnResetFailed")?.addEventListener("click", () =>
	action("/reset-failed", {}, "Reset all failed entries to pending?"),
);
$("#btnResetFailedInline")?.addEventListener("click", () =>
	action("/reset-failed", {}, "Reset all failed entries to pending?"),
);
$("#btnScanReport")?.addEventListener("click", openScanModal);
$("#btnCloseScanModal")?.addEventListener("click", closeScanModal);
$("#btnCloseScanModal2")?.addEventListener("click", closeScanModal);
$("#btnTranscodeTop10")?.addEventListener("click", () => {
	closeScanModal();
	action("/transcode", { limit: 10, force: false }, "Transcode top 10 candidates?");
});
$("#btnTranscodeAllPending")?.addEventListener("click", () => {
	closeScanModal();
	action("/transcode", { force: false }, "Transcode all pending files?");
});
// Close modal on overlay click
$("#scanReportModal")?.addEventListener("click", (e) => {
	if (e.target === $("#scanReportModal")) closeScanModal();
});

/* ---------- SSE ---------- */
function connectEvents() {
	if (eventSource) {
		try {
			eventSource.close();
		} catch (e) {}
	}
	eventSource = new EventSource(`${API}/events`);
	eventSource.onmessage = (e) => {
		try {
			const msg = JSON.parse(e.data);
			if (msg.type === "progress") {
				const r = $("#statusPill");
				r.textContent = msg.data.type || "running";
				r.className = "status-pill running";
				$("#statusDot").className = "brand-dot";
				if (msg.data.current_file) {
					$("#currentFile").textContent = basename(msg.data.current_file);
					$("#currentFile").classList.add("pulse");
					$("#currentShow").textContent = showName(msg.data.current_file);
				} else {
					$("#currentFile").textContent = msg.data.message || "Working...";
					$("#currentFile").classList.remove("pulse");
				}
		} else if (msg.type === "finished") {
			toast(msg.data.message, msg.data.ok ? "ok" : "err");
			loadStatus();
			// Auto-open scan report modal when a scan job finishes
			if (msg.data.message && msg.data.message.toLowerCase().includes("scan")) {
				setTimeout(() => {
					loadScanReport().then(() => openScanModal());
				}, 1000);
			}
			} else if (msg.type === "log") {
				if (currentView === "logs") {
					const term = $("#logTerminal");
					const cls = msg.data.level?.toLowerCase() || "info";
					const line = h(
						"div",
						{ className: `log-line ${cls}` },
						h("span", { className: "text-muted" }, msg.data.time || ""),
						" " + (msg.data.message || ""),
					);
					term.appendChild(line);
					term.scrollTop = term.scrollHeight;
				}
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

	// Close sidebar on outside click (mobile)
	document.addEventListener("click", (e) => {
		const sidebar = $("#sidebar");
		const toggle = $("#mobileToggle");
		if (
			sidebar?.classList.contains("open") &&
			!sidebar.contains(e.target) &&
			!toggle.contains(e.target)
		) {
			sidebar.classList.remove("open");
		}
	});
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", init);
} else {
	init();
}
