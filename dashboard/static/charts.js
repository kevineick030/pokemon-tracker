/* Chart.js-Helfer + Vanilla-JS-Interaktionen für das Dashboard. */

const THEME = {
  accent: "#e94560",
  accentSoft: "rgba(233, 69, 96, 0.15)",
  grid: "rgba(255, 255, 255, 0.08)",
  text: "#cfcfe6",
};

function _baseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: THEME.grid }, ticks: { color: THEME.text } },
      y: { grid: { color: THEME.grid }, ticks: { color: THEME.text } },
    },
  };
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
        backgroundColor: THEME.accentSoft,
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        pointRadius: 2,
        pointBackgroundColor: THEME.accent,
      }],
    },
    options: _baseOptions(),
  });
  return el._chart;
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
