/* Chart.js-Helfer + Vanilla-JS-Interaktionen für das Dashboard. */

const THEME = {
  accent: "#e94560",
  accent2: "#7c5cff",
  accentSoft: "rgba(233, 69, 96, 0.15)",
  grid: "rgba(255, 255, 255, 0.06)",
  text: "#9aa0bf",
};

// Globale, dezente Chart.js-Defaults (modernes Look & Feel)
if (window.Chart) {
  Chart.defaults.font.family =
    "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif";
  Chart.defaults.color = THEME.text;
  Chart.defaults.plugins.tooltip.backgroundColor = "rgba(18,19,32,0.95)";
  Chart.defaults.plugins.tooltip.borderColor = "rgba(255,255,255,0.12)";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 12;
  Chart.defaults.plugins.tooltip.cornerRadius = 10;
  Chart.defaults.plugins.tooltip.displayColors = false;
  Chart.defaults.plugins.tooltip.titleColor = "#fff";
  Chart.defaults.plugins.tooltip.bodyColor = "#cfd2e6";
}

function _baseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    animation: { duration: 900, easing: "easeOutQuart" },
    animations: {
      // Linie „wächst" sanft von unten herein
      y: { from: (ctx) => (ctx.chart.chartArea ? ctx.chart.chartArea.bottom : 0) },
    },
    plugins: { legend: { display: false } },
    scales: {
      x: {
        grid: { display: false },
        ticks: { color: THEME.text, maxRotation: 0, autoSkip: true, maxTicksLimit: 7 },
        border: { display: false },
      },
      y: {
        grid: { color: THEME.grid, drawTicks: false },
        ticks: {
          color: THEME.text, padding: 8,
          callback: (v) => (v >= 1000 ? (v / 1000).toFixed(1) + "k" : v) + " €",
        },
        border: { display: false },
      },
    },
  };
}

// Vertikaler Verlauf vom Akzent (oben) ins Transparente (unten)
function _areaGradient(ctx, area, color) {
  const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  g.addColorStop(0, "rgba(233,69,96,0.35)");
  g.addColorStop(0.6, "rgba(233,69,96,0.08)");
  g.addColorStop(1, "rgba(233,69,96,0)");
  return g;
}

function renderLineChart(canvasId, labels, data, label) {
  const el = document.getElementById(canvasId);
  if (!el) return null;
  // vorhandenes Chart auf dem Canvas zerstören (z.B. Watchlist-Wechsel)
  if (el._chart) el._chart.destroy();
  el._chart = new Chart(el, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        label: label,
        data: data,
        borderColor: THEME.accent,
        backgroundColor: (c) => {
          const { ctx, chartArea } = c.chart;
          if (!chartArea) return THEME.accentSoft;
          return _areaGradient(ctx, chartArea, THEME.accent);
        },
        borderWidth: 2.5,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: "#fff",
        pointHoverBorderColor: THEME.accent,
        pointHoverBorderWidth: 2,
      }],
    },
    options: _baseOptions(),
  });
  return el._chart;
}

/* -------- Wertentwicklung mit Zeitraum-Umschaltung -------- */
// Generisch: lädt bei Klick auf einen Zeitraum-Button neue Daten von `urlBase`
// (z.B. /api/portfolio-chart oder /api/card-chart/5) und zeichnet neu.
function initRangeChart(canvasId, toggleId, urlBase, labels, values) {
  renderLineChart(canvasId, labels, values, "Marktwert (€)");
  const toggle = document.getElementById(toggleId);
  if (!toggle) return;
  toggle.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      toggle.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const days = btn.dataset.days;
      const sep = urlBase.includes("?") ? "&" : "?";
      try {
        const res = await fetch(`${urlBase}${sep}days=${days}`, {
          headers: { "X-Requested-With": "fetch" },
        });
        const d = await res.json();
        renderLineChart(canvasId, d.labels, d.values, "Marktwert (€)");
      } catch (e) {
        console.error("Chart-Update fehlgeschlagen", e);
      }
    });
  });
}

function initPortfolioChart(canvasId, toggleId, labels, values) {
  initRangeChart(canvasId, toggleId, "/api/portfolio-chart", labels, values);
}

function initCardChart(canvasId, toggleId, cardId, labels, values) {
  initRangeChart(canvasId, toggleId, `/api/card-chart/${cardId}`, labels, values);
}

function renderBarChart(canvasId, labels, data, label) {
  const el = document.getElementById(canvasId);
  if (!el) return null;
  if (el._chart) el._chart.destroy();
  el._chart = new Chart(el, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: label,
        data: data,
        backgroundColor: THEME.accent,
        borderRadius: 4,
      }],
    },
    options: _baseOptions(),
  });
  return el._chart;
}

const PALETTE = ["#e94560", "#4ade80", "#60a5fa", "#fbbf24", "#a78bfa",
                 "#f472b6", "#34d399", "#fb923c"];

function renderMultiLineChart(canvasId, series) {
  // series: { retailerName: { labels: [...], values: [...] } }
  const el = document.getElementById(canvasId);
  if (!el) return null;
  if (el._chart) el._chart.destroy();

  // Vereinigte, sortierte X-Achse über alle Händler
  const labelSet = new Set();
  Object.values(series).forEach((s) => s.labels.forEach((l) => labelSet.add(l)));
  const labels = Array.from(labelSet).sort();

  const datasets = Object.entries(series).map(([name, s], i) => {
    const map = {};
    s.labels.forEach((l, idx) => { map[l] = s.values[idx]; });
    return {
      label: name,
      data: labels.map((l) => (l in map ? map[l] : null)),
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: "transparent",
      borderWidth: 2,
      tension: 0.3,
      spanGaps: true,
      pointRadius: 2,
    };
  });

  const opts = _baseOptions();
  opts.plugins.legend = { display: true, labels: { color: THEME.text } };
  el._chart = new Chart(el, { type: "line", data: { labels, datasets }, options: opts });
  return el._chart;
}

function renderSparkline(canvas, data) {
  if (!canvas || !data || data.length === 0) return;
  new Chart(canvas, {
    type: "line",
    data: {
      labels: data.map((_, i) => i),
      datasets: [{
        data: data,
        borderColor: THEME.accent,
        borderWidth: 1.5,
        pointRadius: 0,
        fill: false,
        tension: 0.4,
      }],
    },
    options: {
      responsive: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } },
      elements: { line: { borderJoinStyle: "round" } },
    },
  });
}

/* -------- Sammlung: Filter + Sortierung -------- */
function initCollection() {
  const gallery = document.getElementById("gallery");
  if (!gallery) return;
  const cards = Array.from(gallery.querySelectorAll(".poke-card-wrap"));
  let filter = "all";

  document.querySelectorAll("#filters .chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#filters .chip").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      filter = btn.dataset.filter;
      applyFilter();
    });
  });

  function applyFilter() {
    cards.forEach((c) => {
      const show = filter === "all" || c.dataset.rarity === filter;
      c.style.display = show ? "" : "none";
    });
  }

  const sortSelect = document.getElementById("sortSelect");
  if (sortSelect) {
    sortSelect.addEventListener("change", () => {
      const key = sortSelect.value;
      const sorted = cards.slice().sort((a, b) => {
        if (key === "date") {
          return (b.dataset.date || "").localeCompare(a.dataset.date || "");
        }
        return parseFloat(b.dataset[key] || 0) - parseFloat(a.dataset[key] || 0);
      });
      sorted.forEach((c) => gallery.appendChild(c));
    });
  }
}

/* -------- Watchlist: Sparklines + Chart bei Klick -------- */
function initWatchlist() {
  const rows = Array.from(document.querySelectorAll(".wl-row"));
  if (rows.length === 0) return;

  rows.forEach((row) => {
    const canvas = row.querySelector("canvas.spark");
    let spark = [];
    try { spark = JSON.parse(row.dataset.spark || "[]"); } catch (e) { spark = []; }
    renderSparkline(canvas, spark);

    row.addEventListener("click", () => {
      const name = row.dataset.name;
      document.getElementById("selName").textContent = name;
      const labels = spark.map((_, i) => `T-${spark.length - i}`);
      renderLineChart("wlChart", labels, spark, name);
      rows.forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
    });
  });

  // erste Zeile vorauswählen
  rows[0].click();
}
