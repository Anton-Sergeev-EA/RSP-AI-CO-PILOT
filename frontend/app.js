const API = ""; // тот же ориджин (FastAPI отдаёт и API, и статику)

const statusRu = { OK: "Норма", WARNING: "Внимание", CRITICAL: "Критично" };
const triggerRu = {
  absolute_threshold: "абсолютный порог (зона C)",
  trend: "статистический тренд-детектор",
  none: "не сработал",
};

let currentFleet = [];
let selectedUnitId = null;
let activePlant = null;

async function jget(url) {
  const r = await fetch(API + url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

async function jpost(url, body) {
  const r = await fetch(API + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

// ---------- Plants + fleet ----------

async function loadAll() {
  const [plantsData, fleetData] = await Promise.all([jget("/api/plants"), jget("/api/units")]);
  renderTopbarStats(fleetData);
  renderPlants(plantsData.plants);
  currentFleet = fleetData.units;
  renderFleetGrid(currentFleet);
  if (currentFleet.length) selectUnit(currentFleet[0].unit_id);
}

function renderTopbarStats(data) {
  const wrap = document.getElementById("topbar-stats");
  wrap.innerHTML = "";
  const chips = [
    { label: "Всего агрегатов", num: data.count, cls: "" },
    { label: "Норма", num: data.ok, cls: "ok" },
    { label: "Внимание", num: data.warning, cls: "warning" },
    { label: "Критично", num: data.critical, cls: "critical" },
  ];
  chips.forEach((c) => {
    const chip = el("div", `stat-chip ${c.cls}`);
    chip.appendChild(el("span", "num", c.num));
    chip.appendChild(el("span", "label", c.label));
    wrap.appendChild(chip);
  });
}

function renderPlants(plants) {
  const wrap = document.getElementById("plant-list");
  wrap.innerHTML = "";
  const allRow = el("div", "plant-row" + (activePlant === null ? " active" : ""));
  allRow.appendChild(el("span", "pname", "Все станции"));
  allRow.addEventListener("click", () => { activePlant = null; renderPlants(plants); renderFleetGrid(currentFleet); });
  wrap.appendChild(allRow);

  plants.forEach((p) => {
    const row = el("div", "plant-row" + (activePlant === p.plant ? " active" : ""));
    const left = el("span");
    left.appendChild(el("span", "pname", p.plant));
    left.appendChild(el("span", "ptype", p.plant_type));
    row.appendChild(left);
    const badges = el("div", "pbadges");
    if (p.critical) badges.appendChild(el("span", "plant-badge critical", p.critical));
    if (p.warning) badges.appendChild(el("span", "plant-badge warning", p.warning));
    badges.appendChild(el("span", "plant-badge ok", p.ok));
    row.appendChild(badges);
    row.addEventListener("click", () => { activePlant = p.plant; renderPlants(plants); renderFleetGrid(currentFleet); });
    wrap.appendChild(row);
  });
}

function renderFleetGrid(units) {
  const grid = document.getElementById("fleet-grid");
  grid.innerHTML = "";
  const filtered = activePlant ? units.filter((u) => u.plant === activePlant) : units;
  filtered.forEach((u) => {
    const card = el("div", `unit-card status-${u.health_status}`);
    card.dataset.unitId = u.unit_id;
    const top = el("div", "unit-card-top");
    top.appendChild(el("span", "unit-id", `${u.unit_id} · ${u.plant}`));
    top.appendChild(el("span", `unit-badge ${u.health_status}`, statusRu[u.health_status] || u.health_status));
    card.appendChild(top);
    const meta = el("div", "unit-meta");
    meta.appendChild(el("span", null, `${u.rated_capacity_mw.toFixed(0)} МВт`));
    meta.appendChild(el("span", `unit-zone zone-${u.current_vibration_zone}`, `Зона ${u.current_vibration_zone}`));
    card.appendChild(meta);
    card.addEventListener("click", () => selectUnit(u.unit_id));
    grid.appendChild(card);
  });
}

function markSelected(unitId) {
  document.querySelectorAll(".unit-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.unitId === unitId);
  });
}

async function selectUnit(unitId) {
  selectedUnitId = unitId;
  markSelected(unitId);
  const panel = document.getElementById("detail-panel");
  panel.innerHTML = '<div class="empty-state small">Загрузка данных агрегата...</div>';

  const unit = await jget(`/api/units/${unitId}`);
  document.getElementById("detail-hint").textContent = `${unit.unit_id} — ${unit.plant}`;

  panel.innerHTML = "";

  const head = el("div", "passport-head");
  head.appendChild(el("div", "passport-title", `${unit.unit_id} — ${unit.plant}`));
  head.appendChild(el("div", "passport-sub",
    `${unit.plant_type} · установленная мощность ${unit.rated_capacity_mw.toFixed(0)} МВт · год ввода ${unit.commissioned_year}`));
  panel.appendChild(head);

  const cpp = unit.cpp_realtime_analysis || {};
  const ai = unit.ai_early_warning || {};

  const kpiRow = el("div", "kpi-row");
  const statusKpi = kpi("Статус", statusRu[unit.health_status] || unit.health_status);
  statusKpi.classList.add(`status-${unit.health_status}`);
  kpiRow.appendChild(statusKpi);
  kpiRow.appendChild(kpi("Текущая зона вибрации", unit.current_vibration_zone));
  kpiRow.appendChild(kpi("AI early-warning", ai.probability != null ? `${Math.round(ai.probability * 100)}%` : "н/д"));
  kpiRow.appendChild(kpi("Срабатывание детектора", triggerRu[cpp.trigger] || "н/д"));
  if (unit.lead_time_days_vs_classic) {
    kpiRow.appendChild(kpi("Выигрыш во времени", `+${unit.lead_time_days_vs_classic} дн.`));
  }
  if (cpp.rul_days_estimate && cpp.rul_days_estimate > 0) {
    kpiRow.appendChild(kpi("Оценка остаточного ресурса", `~${Math.round(cpp.rul_days_estimate)} дн.`));
  }
  panel.appendChild(kpiRow);

  panel.appendChild(el("div", "section-title", "Виброскорость подшипников, мм/с (СКЗ)"));
  const chartWrap1 = el("div", "chart-wrap");
  chartWrap1.appendChild(lineChartSVG(unit.telemetry.days, unit.telemetry.vibration_mm_s, {
    markDay: unit.telemetry.will_degrade ? unit.telemetry.degrade_start_day : null,
    anomalyDay: (cpp.trend_anomaly_day > 0) ? cpp.trend_anomaly_day : (cpp.absolute_threshold_day > 0 ? cpp.absolute_threshold_day : null),
    thresholdLine: 4.5,
  }));
  panel.appendChild(chartWrap1);

  panel.appendChild(el("div", "section-title", "Температура подшипника, °C"));
  const chartWrap2 = el("div", "chart-wrap");
  chartWrap2.appendChild(lineChartSVG(unit.telemetry.days, unit.telemetry.bearing_temp_c, {
    markDay: unit.telemetry.will_degrade ? unit.telemetry.degrade_start_day : null,
  }));
  panel.appendChild(chartWrap2);

  panel.appendChild(el("div", "section-title", "AI-объяснение (спросите копайлота справа для подробностей)"));
  const box = el("div", "explain-box",
    unit.health_status === "OK"
      ? "Классические детекторы и AI-модель раннего предупреждения не фиксируют признаков деградации. Агрегат в штатном режиме эксплуатации."
      : `Обнаружено отклонение — подробное объяснение с указанием источников доступно в чате копайлота справа (спросите «что с ${unit.unit_id}?»).`
  );
  panel.appendChild(box);
}

function kpi(label, num) {
  const k = el("div", "kpi");
  k.appendChild(el("div", "num", num));
  k.appendChild(el("div", "label", label));
  return k;
}

function lineChartSVG(days, values, opts = {}) {
  const w = 720, h = 160, padL = 34, padR = 10, padT = 10, padB = 20;
  const allVals = opts.thresholdLine != null ? [...values, opts.thresholdLine] : values;
  const minV = Math.min(...allVals) - 0.3, maxV = Math.max(...allVals) + 0.3;
  const x = (i) => padL + (i / (days.length - 1)) * (w - padL - padR);
  const y = (v) => padT + (1 - (v - minV) / (maxV - minV)) * (h - padT - padB);

  let path = "";
  values.forEach((v, i) => {
    path += (i === 0 ? "M" : "L") + x(i).toFixed(1) + "," + y(v).toFixed(1) + " ";
  });

  const svgns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgns, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", h);

  for (let i = 0; i <= 3; i++) {
    const gy = padT + (i / 3) * (h - padT - padB);
    const line = document.createElementNS(svgns, "line");
    line.setAttribute("x1", padL); line.setAttribute("x2", w - padR);
    line.setAttribute("y1", gy); line.setAttribute("y2", gy);
    line.setAttribute("stroke", "#24314d"); line.setAttribute("stroke-width", "1");
    svg.appendChild(line);
    const val = maxV - (i / 3) * (maxV - minV);
    const label = document.createElementNS(svgns, "text");
    label.setAttribute("x", 4); label.setAttribute("y", gy + 4);
    label.setAttribute("fill", "#8fa0c2"); label.setAttribute("font-size", "10");
    label.textContent = val.toFixed(1);
    svg.appendChild(label);
  }

  if (opts.thresholdLine != null) {
    const ty = y(opts.thresholdLine);
    const line = document.createElementNS(svgns, "line");
    line.setAttribute("x1", padL); line.setAttribute("x2", w - padR);
    line.setAttribute("y1", ty); line.setAttribute("y2", ty);
    line.setAttribute("stroke", "#ff5c72"); line.setAttribute("stroke-width", "1");
    line.setAttribute("stroke-dasharray", "3 3");
    svg.appendChild(line);
  }

  if (opts.markDay != null) {
    const lx = x(opts.markDay);
    const line = document.createElementNS(svgns, "line");
    line.setAttribute("x1", lx); line.setAttribute("x2", lx);
    line.setAttribute("y1", padT); line.setAttribute("y2", h - padB);
    line.setAttribute("stroke", "#f5b642"); line.setAttribute("stroke-width", "1.5");
    line.setAttribute("stroke-dasharray", "4 3");
    svg.appendChild(line);
  }
  if (opts.anomalyDay != null) {
    const lx = x(opts.anomalyDay);
    const line = document.createElementNS(svgns, "line");
    line.setAttribute("x1", lx); line.setAttribute("x2", lx);
    line.setAttribute("y1", padT); line.setAttribute("y2", h - padB);
    line.setAttribute("stroke", "#ff5c72"); line.setAttribute("stroke-width", "1.5");
    svg.appendChild(line);
  }
  const ox = x(Math.min(90, days.length - 1));
  const oline = document.createElementNS(svgns, "line");
  oline.setAttribute("x1", ox); oline.setAttribute("x2", ox);
  oline.setAttribute("y1", padT); oline.setAttribute("y2", h - padB);
  oline.setAttribute("stroke", "#34c3ff"); oline.setAttribute("stroke-width", "1");
  oline.setAttribute("stroke-dasharray", "2 3");
  svg.appendChild(oline);

  const p = document.createElementNS(svgns, "path");
  p.setAttribute("d", path.trim());
  p.setAttribute("fill", "none");
  p.setAttribute("stroke", "#34c3ff");
  p.setAttribute("stroke-width", "2");
  svg.appendChild(p);

  const legend = el("div", "hint");
  legend.style.marginTop = "6px";
  let legendHtml = `<span style="color:#34c3ff">┊</span> окно AI (90 дн.) &nbsp; <span style="color:#f5b642">┊</span> факт. начало деградации &nbsp; <span style="color:#ff5c72">┃</span> обнаружение детектором`;
  if (opts.thresholdLine != null) legendHtml += ` &nbsp; <span style="color:#ff5c72">┈</span> абсолютный порог (зона C)`;
  legend.innerHTML = legendHtml;

  const wrapDiv = document.createElement("div");
  wrapDiv.appendChild(svg);
  wrapDiv.appendChild(legend);
  return wrapDiv;
}

// ---------- C++ benchmark ----------

document.getElementById("bench-btn").addEventListener("click", async () => {
  const box = document.getElementById("bench-result");
  box.innerHTML = "Выполняется...";
  const data = await jget("/api/engine/benchmark?n=2000000");
  box.innerHTML = `
    <div class="big">${Math.round(data.throughput_points_per_sec).toLocaleString("ru-RU")} точек/сек</div>
    <div>${data.benchmark_points.toLocaleString("ru-RU")} измерений телеметрии обработано за ${data.elapsed_seconds.toFixed(3)} сек — движок способен работать на недорогом edge-устройстве рядом со шкафом управления, без постоянного канала в облако.</div>
  `;
});

// ---------- Copilot chat ----------

const SUGGESTIONS = [
  "какие агрегаты сейчас в критическом состоянии?",
  "что такое зона C?",
  "как работает модель раннего предупреждения?",
  "что ты умеешь?",
];

function renderSuggestions() {
  const wrap = document.getElementById("chat-suggestions");
  wrap.innerHTML = "";
  SUGGESTIONS.forEach((s) => {
    const chip = el("button", "chip-btn", s);
    chip.type = "button";
    chip.addEventListener("click", () => askCopilot(s));
    wrap.appendChild(chip);
  });
}

function appendChatMsg(role, html) {
  const log = document.getElementById("chat-log");
  const msg = el("div", `chat-msg ${role}`, html);
  log.appendChild(msg);
  log.scrollTop = log.scrollHeight;
  return msg;
}

async function askCopilot(question) {
  document.getElementById("chat-input").value = "";
  appendChatMsg("user", escapeHtml(question));
  const thinking = appendChatMsg("bot", "…");
  try {
    const res = await jpost("/api/copilot/ask", { question });
    thinking.innerHTML = `<div class="chat-intent">${res.intent || ""}</div>${escapeHtml(res.answer)}`;
    if (res.sources && res.sources.length) {
      thinking.innerHTML += `<div class="chat-sources">Источники: ${res.sources.join(", ")}</div>`;
    }
  } catch (e) {
    thinking.innerHTML = "Не удалось получить ответ. Попробуйте ещё раз.";
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

document.getElementById("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const q = input.value.trim();
  if (q) askCopilot(q);
});

renderSuggestions();
appendChatMsg("bot", "Здравствуйте! Я копайлот дежурного диспетчера RSP. Спросите о состоянии конкретного агрегата, станции или о логике мониторинга.");

loadAll();
