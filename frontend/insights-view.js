import { animateLineChart, lineChart } from "./charts.js";
import { showToast } from "./toast.js";
import { connectQueueSocket } from "./ws-client.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

// Duplicated (not imported) from the backend's provider map -- same
// pragmatic choice settings-view.js and history-view.js already make;
// there's no shared frontend/backend module in this project.
const PROVIDER_DISPLAY_NAMES = {
  gemini: "Gemini",
  anthropic: "Anthropic",
  openai: "OpenAI",
  groq: "Groq",
  openrouter: "OpenRouter",
};

const OPERATION_LABELS = {
  transcribe_chunk: "Transcription",
  summarize: "Summaries",
  chat: "Lesson chat",
  course_chat: "Course chat",
  course_digest: "Study guides",
};

const rangeButtons = Array.from(document.querySelectorAll(".insights-range__btn"));
const refreshBtn = document.getElementById("insights-refresh-btn");
const exportBtn = document.getElementById("insights-export-btn");
const kpiGrid = document.getElementById("insights-kpi-grid");
const recommendationEl = document.getElementById("insights-recommendation");
const costWarningEl = document.getElementById("insights-cost-warning");
const proPanels = document.getElementById("insights-pro-panels");
const lockOverlay = document.getElementById("insights-lock-overlay");
const upgradeBtn = document.getElementById("insights-upgrade-btn");
const chartContainer = document.getElementById("insights-trend-chart");
const operationList = document.getElementById("insights-operation-list");
const videoList = document.getElementById("insights-video-list");
const videosToggle = document.getElementById("insights-videos-toggle");
const providerList = document.getElementById("insights-provider-list");
const insightsNavBtn = document.querySelector('.nav-btn[data-view="view-insights"]');

let currentRange = "30d";

// A provider bills either by tokens (gemini / anthropic / openai-summary /
// openrouter) or by audio duration (openai-whisper / groq transcription,
// which report audio_seconds with the token fields at 0) — never assume
// tokens are present. Show whichever this provider actually reported, then
// fall back to calls alone if it somehow reported neither. (Duplicated from
// the pre-move version in settings-view.js -- same reasoning as the
// PROVIDER_DISPLAY_NAMES duplication above.)
function formatUsageMeta(stats) {
  const parts = [];
  if (stats.total_tokens > 0) {
    parts.push(`${stats.total_tokens.toLocaleString()} tokens`);
  } else if (stats.audio_seconds > 0) {
    parts.push(`${(stats.audio_seconds / 60).toFixed(1)} min audio`);
  }
  const calls = stats.calls ?? 0;
  parts.push(`${calls.toLocaleString()} ${calls === 1 ? "call" : "calls"}`);
  return parts.join(" · ");
}

// null cost means the pricing table had no entry for this provider/model —
// surface "cost unknown" rather than a misleading "$0.00" / "$null". Four
// decimals since these can be fractions of a cent.
function formatCost(cost) {
  return cost == null ? "cost unknown" : `$${cost.toFixed(4)}`;
}

function renderUsageRowList(container, entries, emptyMessage) {
  container.innerHTML = "";
  if (entries.length === 0) {
    const p = document.createElement("p");
    p.className = "usage-empty";
    p.textContent = emptyMessage;
    container.appendChild(p);
    return;
  }
  entries.forEach(({ name, title, stats }) => {
    const row = document.createElement("div");
    row.className = "usage-row";

    const nameEl = document.createElement("span");
    nameEl.className = "usage-row__name";
    nameEl.textContent = name;
    if (title) nameEl.title = title;

    const meta = document.createElement("span");
    meta.className = "usage-row__meta";
    meta.textContent = formatUsageMeta(stats);

    const cost = document.createElement("span");
    cost.className = "usage-row__cost";
    cost.textContent = formatCost(stats.estimated_cost_usd);

    row.append(nameEl, meta, cost);
    container.appendChild(row);
  });
}

function renderProviders(providers) {
  const entries = Object.entries(providers || {}).map(([provider, stats]) => ({
    name: PROVIDER_DISPLAY_NAMES[provider] || provider,
    stats,
  }));
  renderUsageRowList(
    providerList,
    entries,
    "No AI usage recorded yet — transcribe or summarize a video to see costs here."
  );
}

function renderOperations(operations) {
  const entries = (operations || []).map((op) => ({
    name: OPERATION_LABELS[op.operation] || op.operation,
    stats: op,
  }));
  renderUsageRowList(operationList, entries, "No AI usage recorded yet.");
}

function renderVideos(videos) {
  const entries = (videos || []).map((video) => ({
    name: video.title || "Removed from history",
    title: video.url || "",
    stats: video,
  }));
  renderUsageRowList(videoList, entries, "No per-video usage recorded yet.");
}

// Computes the KPI row from the always-free /usage payload. Everything here
// is real except videos_processed -- distinct-video counting needs a DB
// query that only the Pro-gated /usage/dashboard endpoint runs, so free
// users see that one tile locked rather than a fabricated number.
function computeFreeKpis(usageData) {
  const providers = Object.values(usageData?.providers || {});
  const audioSeconds = providers.reduce((sum, stats) => sum + (stats.audio_seconds || 0), 0);
  let costTotal = 0;
  let anyCostKnown = false;
  providers.forEach((stats) => {
    if (stats.estimated_cost_usd != null) {
      costTotal += stats.estimated_cost_usd;
      anyCostKnown = true;
    }
  });
  return {
    hours_processed: audioSeconds / 3600,
    videos_processed: null,
    total_calls: usageData?.total_calls || 0,
    estimated_cost_usd: anyCostKnown ? costTotal : null,
  };
}

// `hourlyRate` (from Settings, optional) turns the already-free
// hours_processed number into a "time saved" dollar figure -- purely a
// client-side multiplication, no new backend data needed, so this tile is
// never Pro-gated. Placed among the headline tiles (value-first framing),
// not tacked onto the end.
function renderKpis(kpis, hourlyRate) {
  kpiGrid.innerHTML = "";
  const tiles = [
    { value: kpis.hours_processed.toFixed(1), label: "Hours processed", headline: true },
    kpis.videos_processed == null
      ? { value: "Pro", label: "Videos processed", headline: true, locked: true }
      : { value: kpis.videos_processed.toLocaleString(), label: "Videos processed", headline: true },
    { value: kpis.total_calls.toLocaleString(), label: "AI calls" },
    { value: formatCost(kpis.estimated_cost_usd), label: "Estimated cost" },
  ];
  if (hourlyRate) {
    const saved = kpis.hours_processed * hourlyRate;
    tiles.splice(2, 0, { value: `$${saved.toFixed(0)}`, label: "Time saved", headline: true });
  }
  tiles.forEach((tile) => {
    const el = document.createElement("div");
    el.className = tile.headline ? "kpi-tile kpi-tile--headline" : "kpi-tile";
    if (tile.locked) el.classList.add("kpi-tile--locked");
    const value = document.createElement("span");
    value.className = "kpi-tile__value";
    value.textContent = tile.value;
    const label = document.createElement("span");
    label.className = "kpi-tile__label";
    label.textContent = tile.label;
    el.append(value, label);
    kpiGrid.appendChild(el);
  });
}

function renderTrendChart(values, { blurred = false } = {}) {
  chartContainer.innerHTML = "";
  // A single point can't draw a line -- lineChart() would only be able to
  // place its "live" dot marker with no visible trend around it, which reads
  // as a rendering bug rather than "not much data yet."
  if (!values || values.length < 2) {
    const p = document.createElement("p");
    p.className = "usage-empty";
    p.textContent = "Not enough usage yet to chart a trend.";
    chartContainer.appendChild(p);
    return;
  }
  const svg = lineChart(values, { width: 600, height: 160, blurred });
  chartContainer.appendChild(svg);
  // Must run after the svg is attached above -- animateLineChart measures
  // the real, laid-out path length, which a detached element doesn't have.
  animateLineChart(svg);
}

function renderRecommendation(text) {
  if (!text) {
    recommendationEl.hidden = true;
    recommendationEl.textContent = "";
    return;
  }
  recommendationEl.hidden = false;
  recommendationEl.textContent = text;
}

function formatRecommendationSentence(rec) {
  const opLabel = (OPERATION_LABELS[rec.operation] || rec.operation).toLowerCase();
  const cheaperName = PROVIDER_DISPLAY_NAMES[rec.cheaper_provider] || rec.cheaper_provider;
  const currentName = rec.current_provider
    ? PROVIDER_DISPLAY_NAMES[rec.current_provider] || rec.current_provider
    : "your current provider";
  const pct = rec.savings_pct != null ? ` (about ${rec.savings_pct}% less)` : "";
  return (
    `For your ${opLabel}, ${cheaperName} would have cost about $${rec.savings_usd.toFixed(4)} ` +
    `less${pct} than ${currentName} over this range.`
  );
}

function setLocked(locked) {
  proPanels.classList.toggle("is-locked", locked);
  lockOverlay.hidden = !locked;
}

// -- data loading -----------------------------------------------------------

async function loadInsights() {
  try {
    const [usageResponse, settingsResponse] = await Promise.all([
      fetch(`${API_BASE}/usage`),
      // Best-effort: only used for the optional "time saved" tile, so a
      // failure here shouldn't take down the rest of the page.
      fetch(`${API_BASE}/settings`).catch(() => null),
    ]);
    if (!usageResponse.ok) throw new Error(`usage fetch failed: ${usageResponse.status}`);
    const usageData = await usageResponse.json();
    renderProviders(usageData.providers);

    const hourlyRate =
      settingsResponse && settingsResponse.ok ? (await settingsResponse.json()).time_saved_hourly_rate : null;

    const dashboardResponse = await fetch(`${API_BASE}/usage/dashboard?range=${currentRange}`);

    if (dashboardResponse.status === 402) {
      const body = await dashboardResponse.json().catch(() => null);
      const preview = body?.detail?.preview;
      setLocked(true);
      renderKpis(computeFreeKpis(usageData), hourlyRate);
      renderRecommendation(preview?.provider_recommendation_teaser);
      costWarningEl.hidden = true;
      renderTrendChart(preview?.trend_sparkline_shape, { blurred: true });
      operationList.innerHTML = "";
      videoList.innerHTML = "";
      return;
    }

    if (!dashboardResponse.ok) throw new Error(`dashboard fetch failed: ${dashboardResponse.status}`);
    const dashboardData = await dashboardResponse.json();

    setLocked(false);
    renderKpis(dashboardData.kpis, hourlyRate);
    renderRecommendation(
      dashboardData.provider_recommendation ? formatRecommendationSentence(dashboardData.provider_recommendation) : null
    );
    costWarningEl.hidden = !dashboardData.cost_data_incomplete;
    renderTrendChart(dashboardData.daily.map((day) => (day.total_tokens || 0) + (day.audio_seconds || 0)));
    renderOperations(dashboardData.operations);
    renderVideos(dashboardData.videos);
  } catch (error) {
    console.warn("Couldn't load Insights:", error);
    renderUsageRowList(providerList, [], "Insights data unavailable.");
  }
}

// -- wiring -------------------------------------------------------------

rangeButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.range === currentRange) return;
    currentRange = btn.dataset.range;
    rangeButtons.forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    loadInsights();
  });
});

refreshBtn.addEventListener("click", async () => {
  refreshBtn.disabled = true;
  refreshBtn.classList.add("is-spinning");
  await loadInsights();
  refreshBtn.classList.remove("is-spinning");
  refreshBtn.disabled = false;
});

upgradeBtn.addEventListener("click", () => {
  document.querySelector('.nav-btn[data-view="view-settings"]')?.click();
});

// The 402 here is the real gate (matches history-view.js's exportNotes
// pattern) -- the button itself stays visible and clickable rather than
// hidden for non-Pro users, so a click still surfaces a friendly upgrade
// prompt instead of the feature just silently not existing.
exportBtn.addEventListener("click", async () => {
  exportBtn.disabled = true;
  try {
    const response = await fetch(`${API_BASE}/usage/export.csv?range=${currentRange}`);
    if (!response.ok) {
      const fallback =
        response.status === 402
          ? "Exporting usage data is a CLIP.PULL Pro feature — upgrade to unlock it."
          : "Failed to export usage data.";
      showToast(fallback, "error");
      return;
    }
    const csvText = await response.text();
    const result = await window.api.saveTextFile(csvText, `clip-pull-usage-${currentRange}.csv`, [
      { name: "CSV", extensions: ["csv"] },
    ]);
    if (result.ok) {
      showToast("Usage data exported.", "success");
      window.api.revealFile(result.path);
    } else if (result.error !== "cancelled") {
      showToast("Failed to save the export.", "error");
    }
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    exportBtn.disabled = false;
  }
});

const toggleVideos = () => {
  const expanding = videoList.hidden;
  videoList.hidden = !expanding;
  videosToggle.setAttribute("aria-expanded", String(expanding));
  videosToggle.classList.toggle("is-expanded", expanding);
};
videosToggle.addEventListener("click", toggleVideos);
videosToggle.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    toggleVideos();
  }
});

// Lazy-loaded on first tab activation, not at app startup -- a free user
// should never trigger a 402 against /usage/dashboard just by opening the
// app if they never visit Insights.
let hasLoadedOnce = false;
insightsNavBtn?.addEventListener("click", () => {
  if (hasLoadedOnce) return;
  hasLoadedOnce = true;
  loadInsights();
});

// -- realtime -----------------------------------------------------------
// The backend broadcasts a payload-less "usage_recorded" event over the
// WebSocket every time any AI feature (transcription, summaries, chat,
// course chat/digest) finishes writing a usage row -- see _record_usage in
// transcription.py. Re-fetching here (rather than trying to patch the
// already-rendered numbers with a client-side delta) keeps this exactly as
// correct as a manual refresh, just without the user having to click one.
const insightsView = document.getElementById("view-insights");
let liveRefreshTimer = null;

function scheduleLiveRefresh() {
  // Only Insights cares about this event, and only while it's actually the
  // visible view -- no point re-fetching and re-animating a chart nobody's
  // looking at. Once opened, hasLoadedOnce stays true, so switching back to
  // Insights later still shows whatever the last refresh (live or manual)
  // left rendered, not a blank state.
  if (!hasLoadedOnce || insightsView?.hidden) return;
  // Debounced: a multi-chunk transcription can fire several usage_recorded
  // events within a couple hundred ms of each other -- coalesce those into
  // one refresh instead of re-fetching (and re-triggering the chart's
  // draw-in animation) once per chunk.
  clearTimeout(liveRefreshTimer);
  liveRefreshTimer = setTimeout(loadInsights, 800);
}

connectQueueSocket((event) => {
  if (event.type === "usage_recorded") scheduleLiveRefresh();
});

// Settings dispatches this the instant a license is activated/deactivated
// (see settings-view.js's renderLicense). Without listening for it, Insights
// had no way to know Pro status changed -- the lock state here is entirely
// driven by whether /usage/dashboard 402s, so it stayed on whatever it last
// rendered until the user happened to hit Refresh, switch date ranges, or an
// unrelated usage_recorded event fired. Matches history-view.js's own
// clippull:license-changed listener for the same reason.
document.addEventListener("clippull:license-changed", () => {
  if (!hasLoadedOnce) return; // never opened yet -- first activation fetches the right state already
  loadInsights();
});
