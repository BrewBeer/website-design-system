// GS – interfejs. Czysty JS (moduł ES), bez frameworka.
// Cel ekranu: w 3 sekundy widać, czy jest sygnał i co dokładnie zrobić.
// Decyzję (KUP / SPRZEDAJ / PLAN / CZEKAJ / POZA OKIENKIEM) liczy serwer: engine/decision.py.

const LC = window.LightweightCharts;
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ------------------------------------------------------------------ stan

const state = {
  config: null,          // /api/config
  instruments: [],       // /api/instruments (rejestr + własne)
  params: {},            // nadpisania parametrów z zakładki „Ustawienia”
  result: null,          // ostatnia odpowiedź /api/analyze
  journal: {},           // notatki: klucz transakcji -> {note}
  selectedSignalId: null,  // transakcja wybrana w dzienniku (zamiast bieżącego sygnału)
  toggles: { sma: false, volume: false, history: false, rejected: false },   // menu „Więcej”
  filters: { A: true, B: true, C: true, long: true, short: true },
  sort: { key: "entry_time", dir: -1 },
  busy: false,
};

// ------------------------------------------------------------------ formatowanie (polskie)

const nf = (digits) => new Intl.NumberFormat("pl-PL", {
  minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: "always",
});
const fmt2 = nf(2), fmt0 = nf(0);
// Liczba miejsc po przecinku zależy od ticka instrumentu (DAX 1, EUR/USD 5).
let fmtPrice = nf(1);
const setPriceDecimals = (d) => { fmtPrice = nf(d); };
const price = (x) => (x == null ? "—" : fmtPrice.format(x));
const num2 = (x) => (x == null ? "—" : fmt2.format(x));
const signedR = (x) => (x > 0 ? "+" : "") + fmt2.format(x) + " R";

// Czas wykresu to czas giełdy zapisany jak UTC, więc czytamy go getterami UTC.
function chartDate(t) {
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())}.${p(d.getUTCMonth() + 1)} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sideMark = (side) => (side === "long" ? "▲" : "▼");
const sideClass = (side) => (side === "long" ? "is-long" : "is-short");
const EXIT = { tp: "TP", sl: "SL", eod: "Koniec sesji" };

// ------------------------------------------------------------------ kolory z tokenów CSS

function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim();
  return { paper: v("--paper"), sheet: v("--sheet"), ink: v("--ink"), graphite: v("--graphite"),
           cobalt: v("--cobalt"), long: v("--long"), short: v("--short"), zone: v("--zone"), candle: v("--candle") };
}

function alpha(hex, a) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

// ------------------------------------------------------------------ połączenie z serwerem GS

// Strona działa najlepiej otwarta z serwera GS (http://localhost:8000). Gdy otworzysz
// index.html inaczej (Live Server, podgląd w VS Code), łączymy się z serwerem na porcie 8000.
const GS_SERVER = "http://localhost:8000";
let API = "";
const api = (path, opts) => fetch(API + path, opts);

async function findServer() {
  for (const base of ["", GS_SERVER]) {   // najpierw ten sam adres co strona, potem port 8000
    try {
      const res = await fetch(base + "/api/config");
      if (res.ok && (res.headers.get("content-type") || "").includes("json")) {
        API = base;
        return res.json();
      }
    } catch { /* ten adres nie odpowiada, próbujemy następny */ }
  }
  throw new Error("brak serwera");
}

async function connect() {
  setVerdictMessage("CZEKAJ", "Łączę się z serwerem GS i sprawdzam, dla których instrumentów są dane…");
  try {
    const cfg = await findServer();
    const [journal, list] = await Promise.all([
      api("/api/journal").then((r) => r.json()).catch(() => ({})),
      api("/api/instruments").then((r) => r.json()),
    ]);
    state.config = cfg;
    state.journal = journal;
    $("#interval").value = cfg.defaults.LTF_INTERVAL;
    setInstruments(list, loadSaved("gs-instrument"));
    renderParams();
    analyze();
  } catch {
    showOffline();
  }
}

function showOffline() {
  setVerdictMessage("BRAK POŁĄCZENIA",
    `Serwer GS nie odpowiada. W terminalu, w folderze <code>gs</code>: <code>.venv\\Scripts\\activate</code>,
     potem <code>uvicorn api.main:app --reload</code>. Otwórz <a href="${GS_SERVER}/">${GS_SERVER}</a>
     albo kliknij ↻, żeby spróbować ponownie.`, true);
}

// Komunikat w pasku decyzji (bez sygnału): łączenie, brak serwera, błąd danych.
function setVerdictMessage(word, html, isHtml = false) {
  $("#verdict").className = "verdict verdict--wait";
  $("#verdict-word").textContent = word;
  if (isHtml) $("#verdict-sentence").innerHTML = html;
  else $("#verdict-sentence").textContent = html;
  $("#verdict-order").hidden = true;
  $("#show-order").hidden = true;
  $("#verdict-age").textContent = "";
}

// ------------------------------------------------------------------ zakładki

function initTabs() {
  const tabs = $$('[role="tab"]');
  const select = (tab) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $("#" + t.getAttribute("aria-controls")).hidden = !on;
    });
    if (tab.id === "tab-btn-signal") scheduleRedraw();   // wykres był ukryty: przelicz drabinę
    if (tab.id === "tab-btn-results" && eqChart) requestAnimationFrame(() => eqChart.timeScale().fitContent());
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t));
    // Strzałki przełączają zakładki (wzorzec ARIA „tabs”).
    t.addEventListener("keydown", (e) => {
      const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d) return;
      const next = tabs[(i + d + tabs.length) % tabs.length];
      next.focus();
      select(next);
    });
  });
  state.showTab = (id) => select($("#tab-btn-" + id));
}

// ------------------------------------------------------------------ wykres (styl stooq)

const chartEl = $("#chart");
const wrapEl = chartEl.parentElement;
let chart, candles, volume, sma15, sma45;
const zonesCanvas = $("#zones");
let signalLines = [], priceTag = null;

function initChart() {
  chart = LC.createChart(chartEl, {
    autoSize: true,
    layout: { background: { type: "solid", color: "rgba(0,0,0,0)" },
              fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif", fontSize: 12 },
    timeScale: { timeVisible: true, secondsVisible: false, barSpacing: 6, minBarSpacing: 3, rightOffset: 6 },
    rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.08 } },
    localization: { locale: "pl-PL", priceFormatter: (p) => price(p) },
    crosshair: { mode: LC.CrosshairMode.Normal },
  });
  // Bieżącą cenę pokazujemy własną linią (czarne pole na osi), żeby legenda była prawdziwa w obu motywach.
  candles = chart.addCandlestickSeries({ priceLineVisible: false, lastValueVisible: false });
  volume = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" },
                                      lastValueVisible: false, priceLineVisible: false, visible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
  const lineOpts = { lineWidth: 1, lineType: LC.LineType.WithSteps, lastValueVisible: false,
                     priceLineVisible: false, crosshairMarkerVisible: false, visible: false };
  sma15 = chart.addLineSeries(lineOpts);
  sma45 = chart.addLineSeries({ ...lineOpts, lineStyle: LC.LineStyle.Dotted });

  chart.timeScale().subscribeVisibleLogicalRangeChange(scheduleRedraw);
  new ResizeObserver(scheduleRedraw).observe(wrapEl);
  // Przeciąganie skali ceny nie ma własnego zdarzenia, więc odświeżamy drabinę po geście.
  ["pointerup", "wheel"].forEach((ev) => wrapEl.addEventListener(ev, scheduleRedraw, { passive: true }));
  applyChartTheme();
}

function applyChartTheme() {
  const t = tokens();
  chart.applyOptions({
    layout: { textColor: t.graphite },
    grid: { vertLines: { color: alpha(t.graphite, 0.08) }, horzLines: { color: alpha(t.graphite, 0.08) } },
    rightPriceScale: { borderColor: alpha(t.graphite, 0.3) },
    timeScale: { borderColor: alpha(t.graphite, 0.3) },
    // Celownik: białe pole na osi i przerywane linie (jak w legendzie).
    crosshair: { vertLine: { color: t.graphite, labelBackgroundColor: "#FFFFFF" },
                 horzLine: { color: t.graphite, labelBackgroundColor: "#FFFFFF" } },
  });
  // Jak na stooq: jeden kolor; wzrostowa pusta (tylko kontur), spadkowa wypełniona.
  candles.applyOptions({ upColor: "rgba(0,0,0,0)", borderUpColor: t.candle, wickUpColor: t.candle,
                         downColor: t.candle, borderDownColor: t.candle, wickDownColor: t.candle });
  sma15.applyOptions({ color: t.cobalt });
  sma45.applyOptions({ color: t.graphite });
  if (state.result) renderChart();
  scheduleRedraw();
}

function renderChartData() {
  const r = state.result;
  const t = tokens();
  const inst = r.meta.instrument;
  candles.applyOptions({ priceFormat: { type: "price", precision: inst.price_decimals, minMove: inst.tick } });
  candles.setData(r.candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
  volume.setData(r.candles.map((c) => ({ time: c.time, value: c.volume, color: alpha(t.graphite, 0.35) })));
  showRecentSessions();
}

// Domyślnie widać tylko bieżącą i poprzednią sesję.
function showRecentSessions() {
  const c = state.result.candles;
  if (!c.length) return;
  const day = (t) => Math.floor(t / 86400);
  const days = [...new Set(c.map((x) => day(x.time)))];
  const fromDay = days[Math.max(0, days.length - 2)];
  const from = c.findIndex((x) => day(x.time) === fromDay);
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, from - 2), to: c.length + 6 });
}

// Wszystko, co zależy od przełączników i motywu (bez ładowania świec od nowa).
function renderChart() {
  const t = tokens();
  candles.applyOptions({ upColor: "rgba(0,0,0,0)", borderUpColor: t.candle, wickUpColor: t.candle,
                         downColor: t.candle, borderDownColor: t.candle, wickDownColor: t.candle });
  volume.applyOptions({ visible: state.toggles.volume });
  renderSma();
  renderPriceTag();
  renderSignal();
  scheduleRedraw();
}

// Bieżąca cena: czarne pole na osi i cienka kropkowana linia.
function renderPriceTag() {
  if (priceTag) candles.removePriceLine(priceTag);
  priceTag = candles.createPriceLine({ price: state.result.meta.last_price, color: "#000000", lineWidth: 1,
                                       lineStyle: LC.LineStyle.Dotted, axisLabelVisible: true, title: "" });
}

function renderSma() {
  const r = state.result;
  const on = state.toggles.sma && r;
  sma15.applyOptions({ visible: !!on, title: r ? `SMA ${r.meta.params.SMA_FAST}` : "" });
  sma45.applyOptions({ visible: !!on, title: r ? `SMA ${r.meta.params.SMA_SLOW}` : "" });
  const pts = r ? r.sma : [];
  const series = (key) => {
    const seen = new Map();
    pts.forEach((p) => seen.set(p.time, { time: p.time, value: p[key] }));
    return [...seen.values()].sort((a, b) => a.time - b.time);
  };
  sma15.setData(series("sma15"));
  sma45.setData(series("sma45"));
}

// Najbliższe strefy: 2 nad ceną i 2 pod. Strefa, w której stoi cena, liczy się do obu stron,
// ale pokazujemy ją raz. Logika ta sama co w zdaniu „Kup, jeśli…” (engine/decision.py).
function nearestZones(count = 2) {
  const r = state.result;
  if (!r) return [];
  const last = r.meta.last_price;
  const above = r.levels.filter((l) => l.high > last).sort((a, b) => a.high - b.high).slice(0, count);
  const below = r.levels.filter((l) => l.low < last).sort((a, b) => b.low - a.low).slice(0, count);
  const seen = new Set();
  return [...above, ...below].filter((l) => !seen.has(l.id) && seen.add(l.id));
}

// „D+H1 ×14” – źródła i łączna liczba dotknięć (×N tylko, gdy w strefie są swingi D lub H1).
function zoneSource(z) {
  const swings = z.sources.some((s) => s === "D" || s === "H1");
  return swings ? `${z.frame} ×${z.touches}` : z.frame;
}

function zoneLabel(z) {
  return `${zoneSource(z)}  ${price(z.low)}–${price(z.high)}`;
}

// Strefa: cienki półprzezroczysty pas między dwiema cienkimi liniami, jeden podpis po prawej.
// Strefa dzienna (zawiera poziom D) ma odrobinę grubsze linie i mocniejszy pas.
function drawZones() {
  const dpr = window.devicePixelRatio || 1;
  const w = wrapEl.clientWidth, h = wrapEl.clientHeight;
  zonesCanvas.width = w * dpr; zonesCanvas.height = h * dpr;
  zonesCanvas.style.width = w + "px"; zonesCanvas.style.height = h + "px";
  const ctx = zonesCanvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!state.result) return;

  const t = tokens();
  const paneW = chart.timeScale().width();
  const paneH = chart.paneSize().height;
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, paneW, paneH);
  ctx.clip();
  ctx.font = "600 12px Archivo, Helvetica Neue, Arial, sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "bottom";
  for (const z of nearestZones()) {
    const yTop = candles.priceToCoordinate(z.high);
    const yBot = candles.priceToCoordinate(z.low);
    if (yTop == null || yBot == null) continue;
    const top = Math.min(yTop, yBot), height = Math.max(Math.abs(yBot - yTop), 1);
    const lw = z.daily ? 1.5 : 1;
    ctx.fillStyle = alpha(t.zone, z.daily ? 0.1 : 0.06);
    ctx.fillRect(0, top, paneW, height);
    ctx.strokeStyle = alpha(t.zone, z.daily ? 1 : 0.75);
    ctx.lineWidth = lw;
    for (const y of [top, top + height]) {
      ctx.beginPath();
      ctx.moveTo(0, Math.round(y) + 0.5);
      ctx.lineTo(paneW, Math.round(y) + 0.5);
      ctx.stroke();
    }
    // Podpis nad pasem (albo pod nim, gdy pas jest przy górnej krawędzi wykresu).
    const label = zoneLabel(z);
    const tw = ctx.measureText(label).width;
    const ly = top > 18 ? top - 3 : top + height + 16;
    ctx.fillStyle = alpha(t.sheet, 0.85);
    ctx.fillRect(paneW - tw - 10, ly - 14, tw + 8, 15);
    ctx.fillStyle = t.ink;
    ctx.fillText(label, paneW - 6, ly);
  }
  ctx.restore();
}

// Sygnał na wykresie: wyraźna strzałka przy świecy sygnałowej i trzy linie (wejście, SL, TP).
// Domyślnie bieżący sygnał z decyzji; po kliknięciu w dzienniku – wybrana transakcja.
function shownSignal() {
  const r = state.result;
  const id = state.selectedSignalId || r?.decision.signal_id;
  return id ? r.signals.find((s) => s.id === id) : null;
}

function renderSignal() {
  const r = state.result;
  const t = tokens();
  signalLines.forEach((pl) => candles.removePriceLine(pl));
  signalLines = [];
  const s = shownSignal();
  const markers = [];

  // Historia z backtestu i odrzucone – tylko na życzenie (menu „Więcej”).
  for (const h of r.signals) {
    if (s && h.id === s.id) continue;
    const rejected = h.status === "rejected";
    if (rejected ? !state.toggles.rejected : !state.toggles.history) continue;
    markers.push({ time: h.time, position: h.side === "long" ? "belowBar" : "aboveBar",
                   shape: h.side === "long" ? "arrowUp" : "arrowDown", size: 1, text: h.setup,
                   color: alpha(t.graphite, rejected ? 0.45 : 0.85) });
  }

  if (s) {
    const color = s.side === "long" ? t.long : t.short;
    const word = state.selectedSignalId ? s.setup : (s.side === "long" ? "KUP" : "SPRZEDAJ");
    markers.push({ time: s.time, position: s.side === "long" ? "belowBar" : "aboveBar",
                   shape: s.side === "long" ? "arrowUp" : "arrowDown", size: 2, color,
                   text: r.decision.state === "plan" && !state.selectedSignalId ? "PLAN" : word });
    const line = (p, c, style, title) => p != null && signalLines.push(candles.createPriceLine({
      price: p, color: c, lineWidth: 2, lineStyle: style, axisLabelVisible: true, title }));
    line(s.entry, t.ink, LC.LineStyle.Dashed, "Wejście");
    line(s.sl, t.short, LC.LineStyle.Solid, "SL");
    line(s.tp, t.long, LC.LineStyle.Solid, "TP");
  }
  candles.setMarkers(markers.sort((a, b) => a.time - b.time));
}

// ------------------------------------------------------------------ pasek decyzji

function renderVerdict() {
  const r = state.result;
  const d = r.decision;
  const m = r.meta;
  const box = $("#verdict");
  // verdict--long/short (a nie is-long), żeby kolor kierunku nie przeszedł na cały tekst paska
  box.className = `verdict verdict--${d.state}` + (d.side ? ` verdict--${d.side}` : "");

  const tag = d.state === "plan"
    ? `<span class="verdict__tag">${d.side === "long" ? "▲ kupno" : "▼ sprzedaż"}</span>` : "";
  $("#verdict-word").innerHTML = esc(d.headline) + tag;
  $("#verdict-sentence").textContent = d.sentence;

  // Przy KUP/SPRZEDAJ tylko liczby potrzebne do zlecenia.
  const order = $("#verdict-order");
  const active = d.state === "buy" || d.state === "sell";
  order.hidden = !active;
  if (active) {
    const o = d.order;
    order.innerHTML = [["Wejście", price(o.activation)], ["Stop loss", price(o.sl)], ["Take profit", price(o.tp)],
                       ["Wielkość", num2(o.size)], ["Ryzyko", `${num2(o.risk_pln)} zł`],
                       ["Możliwy zysk", `${num2(o.reward_pln)} zł`]]
      .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  }
  $("#show-order").hidden = !d.order;
  $("#show-order").textContent = d.state === "plan" ? "Pokaż plan zlecenia" : "Pokaż zlecenie";
  $("#verdict-age").textContent = `${m.name} ${price(m.last_price)} · ${m.window.market_text} · ` +
    `${d.freshness.text}${m.custom_params ? " · ustawienia własne" : ""}`;
}

// ------------------------------------------------------------------ karta zlecenia

function openOrderCard() {
  const d = state.result.decision;
  const o = d.order;
  if (!o) return;
  const raw = (x) => String(x);   // do schowka bez spacji tysięcy, z kropką: przyjmie każda platforma
  const rows = [
    ["Cena aktywacji", price(o.activation), raw(o.activation)],
    ["Stop loss", price(o.sl), raw(o.sl)],
    ["Take profit", price(o.tp), raw(o.tp)],
    ["Wielkość", num2(o.size), raw(o.size)],
    ["Ważność", o.validity, o.validity],
  ];
  $("#order-title").textContent = d.state === "plan" ? "Plan zlecenia" : "Zlecenie";
  $("#order-body").innerHTML = `
    <p class="order-type ${sideClass(o.side)}">${esc(o.type)}<small>${esc(o.type_help)}</small></p>
    ${d.state === "plan" ? `<p class="order-hint"><strong>Najpierw sprawdź na wykresie brokera (na żywo):</strong>
      czy ${esc(o.condition)}. Dane tutaj są opóźnione.</p>` : ""}
    <dl class="order-rows">
      ${rows.map(([k, v, copy]) => `<div class="order-row"><dt>${k}</dt><dd>${esc(v)}</dd>
        <button type="button" class="btn btn--small" data-copy="${esc(copy)}" aria-label="Kopiuj: ${k}">Kopiuj</button></div>`).join("")}
      <div class="order-row"><dt>Ryzyko (gdy zadziała SL)</dt><dd>${num2(o.risk_pln)} zł</dd><span></span></div>
      <div class="order-row"><dt>Możliwy zysk (gdy zadziała TP)</dt><dd>${num2(o.reward_pln)} zł</dd><span></span></div>
    </dl>
    <p class="order-hint">${esc(o.broker_hint)}</p>`;
  $("#order-dialog").showModal();
}

function initOrderCard() {
  $("#show-order").addEventListener("click", openOrderCard);
  $("#order-body").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      btn.textContent = "Skopiowano";
    } catch {
      btn.textContent = "Nie udało się";
    }
    setTimeout(() => { btn.textContent = "Kopiuj"; }, 1500);
  });
  $$("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
}

// ------------------------------------------------------------------ drabina (4 najbliższe poziomy)

let redrawPending = false;
function scheduleRedraw() {
  if (redrawPending) return;
  redrawPending = true;
  requestAnimationFrame(() => { redrawPending = false; drawZones(); renderLadder(); });
}

function renderLadder() {
  const box = $("#ladder");
  box.textContent = "";
  const r = state.result;
  if (!r || $("#tab-signal").hidden) return;
  const zones = nearestZones();
  if (!zones.length) {
    box.innerHTML = `<p class="muted" style="padding-left:8px">Brak kluczowych stref w pobliżu.</p>`;
    return;
  }

  // Obok wykresu: te same piksele co na wykresie. Pod wykresem (wąski ekran): proporcjonalnie.
  const narrow = matchMedia("(max-width: 900px)").matches;
  const paneH = chart.paneSize().height || wrapEl.clientHeight;
  const boxH = box.clientHeight;
  const offset = wrapEl.getBoundingClientRect().top - box.getBoundingClientRect().top;
  const toY = (p) => {
    const y = candles.priceToCoordinate(p);
    if (y == null) return null;
    return narrow ? (y / paneH) * boxH : y + offset;
  };

  // Wiersz stoi na środku strefy; przy nachodzeniu odsuwamy go niżej.
  let prevY = -Infinity;
  const last = r.meta.last_price;
  for (const z of zones.sort((a, b) => b.high - a.high)) {
    let y = toY((z.low + z.high) / 2);
    if (y == null) continue;
    y = Math.max(y, prevY + 22);
    prevY = y;
    if (y < 11 || y > boxH - 11) continue;
    const where = z.low > last ? "Nad ceną" : z.high < last ? "Pod ceną" : "Cena w strefie";
    const el = document.createElement("div");
    el.className = "ladder__row" + (z.daily ? " is-daily" : "");
    el.setAttribute("role", "listitem");
    el.style.top = `${y}px`;
    el.setAttribute("aria-label", `${where}: strefa ${price(z.low)} do ${price(z.high)}, ${zoneSource(z)}`);
    el.innerHTML = `<span class="ladder__price">${price(z.low)}–${price(z.high)}</span><span class="ladder__src">${zoneSource(z)}</span>`;
    box.appendChild(el);
  }

  const py = toY(r.meta.last_price);
  if (py != null && py >= 0 && py <= boxH) {
    const line = document.createElement("div");
    line.className = "ladder__price-line";
    line.style.top = `${py}px`;
    line.innerHTML = `<span>${price(r.meta.last_price)}</span>`;
    box.appendChild(line);
  }
}

// ------------------------------------------------------------------ wyniki (okresy 1M/3M/YTD/rok)

let eqChart = null, eqSeries = null;
state.period = loadSavedEarly("gs-period") || "1m";

function loadSavedEarly(key) { try { return localStorage.getItem(key); } catch { return null; } }

// Krzywa kapitału w R: zielona nad zerem, czerwona pod zerem.
function initEquityChart() {
  eqChart = LC.createChart($("#equity-chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: "rgba(0,0,0,0)" },
              fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif", fontSize: 12 },
    timeScale: { timeVisible: false, borderVisible: false },
    rightPriceScale: { borderVisible: false },
    localization: { locale: "pl-PL", priceFormatter: (v) => `${fmt2.format(v)} R` },
    handleScroll: false, handleScale: false,
  });
  eqSeries = eqChart.addBaselineSeries({ baseValue: { type: "price", price: 0 }, lineWidth: 2,
                                         priceLineVisible: false, lastValueVisible: true });
  applyEquityTheme();
}

function applyEquityTheme() {
  if (!eqChart) return;
  const t = tokens();
  eqChart.applyOptions({
    layout: { textColor: t.graphite },
    grid: { vertLines: { visible: false }, horzLines: { color: alpha(t.graphite, 0.1) } },
    crosshair: { vertLine: { labelBackgroundColor: "#FFFFFF" }, horzLine: { labelBackgroundColor: "#FFFFFF" } },
  });
  eqSeries.applyOptions({
    topLineColor: t.long, topFillColor1: alpha(t.long, 0.28), topFillColor2: alpha(t.long, 0.04),
    bottomLineColor: t.short, bottomFillColor1: alpha(t.short, 0.04), bottomFillColor2: alpha(t.short, 0.28),
  });
}

function renderBacktest() {
  const box = $("#backtest");
  const perf = state.result.performance;
  if (!state.result.meta.intraday || !perf) {
    box.innerHTML = `<p class="muted">Wyniki wymagają danych 5-minutowych.</p>`;
    $("#coverage").hidden = true;
    return;
  }
  $$("[data-period]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.period === state.period)));
  const p = perf.periods.find((x) => x.key === state.period) || perf.periods[0];
  const b = p.stats;

  // Uczciwie: ile dni okresu naprawdę ma dane 5m.
  const cov = $("#coverage");
  cov.hidden = p.complete;
  if (!p.complete) {
    const from = p.data_from ? new Date(p.data_from).toLocaleDateString("pl-PL") : null;
    cov.textContent = p.days_with_data === 0
      ? "Brak danych 5-minutowych w tym okresie."
      : `Dane 5-minutowe są tylko z ${p.days_with_data} z ${p.days_expected} dni handlowych tego okresu ` +
        `(od ${from}). Yahoo udostępnia świece 5m tylko z ok. 60 ostatnich dni, a aplikacja zapisuje ` +
        `każdy nowy dzień w archiwum na dysku – z czasem ten okres się wypełni. Wynik dotyczy tylko dni z danymi.`;
  }

  if (!eqChart) initEquityChart();
  eqSeries.setData(p.equity);
  eqChart.timeScale().fitContent();
  $("#equity-empty").hidden = b.trades > 0;

  const group = (obj, label) => Object.entries(obj).map(([k, v]) =>
    `<dt>${label(k)}</dt><dd>${v.trades} · ${fmt0.format(v.winrate)}% · <span class="${v.sum_r < 0 ? "is-short" : ""}">${signedR(v.sum_r)}</span></dd>`).join("");
  const verdict = b.trades === 0
    ? `<p class="verdict-line muted">Brak transakcji w tym okresie.</p>`
    : b.sum_r < 0
      ? `<p class="verdict-line is-short">W tej konfiguracji strategia traci.</p>`
      : `<p class="verdict-line">Wynik dodatni na danych historycznych. To nie gwarantuje wyników w przyszłości.</p>`;
  const pln = `${p.sum_pln > 0 ? "+" : ""}${fmt2.format(p.sum_pln)} zł`;

  box.innerHTML = `<div class="bt-grid">
    <div>
      <dl class="rows">
        <dt>Transakcje</dt><dd>${b.trades}</dd>
        <dt>Zyskowne</dt><dd>${fmt0.format(b.winrate)}%</dd>
        <dt>Suma</dt><dd class="${b.sum_r < 0 ? "is-short" : ""}">${signedR(b.sum_r)}
          <span class="pln">≈ ${pln} przy ryzyku ${fmt2.format(perf.risk_pln)} zł na transakcję</span></dd>
        <dt>Średni wynik</dt><dd>${signedR(b.avg_r)}</dd>
        <dt>Maks. obsunięcie</dt><dd>${fmt2.format(b.max_dd_r)} R</dd>
        <dt>Najdłuższa seria strat</dt><dd>${b.max_loss_streak}</dd>
      </dl>
      ${verdict}
    </div>
    <div>
      <h3 class="subhead" style="margin-top:0">Wzorce</h3>
      <dl class="rows">${group(b.by_setup, (k) => `Wzorzec ${k}`) || "<dt>—</dt><dd></dd>"}</dl>
      <h3 class="subhead">Kierunki</h3>
      <dl class="rows">${group(b.by_side, (k) => `${sideMark(k)} ${k}`) || "<dt>—</dt><dd></dd>"}</dl>
    </div>
  </div>`;
}

function initResults() {
  $$("[data-period]").forEach((btn) => btn.addEventListener("click", () => {
    state.period = btn.dataset.period;
    save("gs-period", state.period);
    if (state.result) renderBacktest();
  }));
}

// ------------------------------------------------------------------ dziennik

function renderJournal() {
  const body = $("#journal-body");
  const r = state.result;
  if (!r) return;
  const f = state.filters;
  const { key, dir } = state.sort;
  const rows = r.trades
    .filter((t) => f[t.setup] && f[t.side])
    .sort((a, b) => (a[key] > b[key] ? 1 : a[key] < b[key] ? -1 : 0) * dir);

  $$(".journal th").forEach((th) => {
    const btn = th.querySelector("button");
    if (!btn) return;
    if (btn.dataset.sort === key) th.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  });

  if (!rows.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="9">Brak transakcji dla wybranych filtrów.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((t) => `
    <tr data-signal="${t.signal_id}" tabindex="0" title="Pokaż na wykresie"
        aria-label="Transakcja ${t.id}, ${t.setup} ${t.side}, wynik ${signedR(t.result_r)}. Enter: pokaż na wykresie">
      <td>${chartDate(t.entry_time)}</td>
      <td>${t.setup}</td>
      <td class="${sideClass(t.side)}">${sideMark(t.side)} ${t.side}</td>
      <td class="num">${price(t.entry)}</td>
      <td class="num">${price(t.sl)}</td>
      <td class="num">${price(t.tp)}</td>
      <td class="num ${t.result_r < 0 ? "is-short" : t.result_r > 0 ? "is-long" : ""}">${signedR(t.result_r)}</td>
      <td>${EXIT[t.exit_reason]}</td>
      <td><textarea rows="1" data-key="${esc(t.key)}" aria-label="Notatka do transakcji ${t.id}"
        placeholder="Co widziałem, co zrobiłbym inaczej">${esc(state.journal[t.key]?.note || "")}</textarea></td>
    </tr>`).join("");
}

// Kliknięcie transakcji: przejście do wykresu z jej wejściem, SL i TP.
function showTrade(signalId) {
  state.selectedSignalId = signalId;
  state.showTab("signal");
  renderSignal();
  const s = state.result.signals.find((x) => x.id === signalId);
  const trade = state.result.trades.find((t) => t.signal_id === signalId);
  if (s) chart.timeScale().setVisibleRange({ from: s.time - 3 * 3600, to: (trade ? trade.exit_time : s.time) + 2 * 3600 });
  $("#verdict-age").textContent = `Na wykresie: transakcja z ${chartDate(s.time)}. Kliknij ↻, żeby wrócić do bieżącego sygnału.`;
}

function initJournal() {
  const body = $("#journal-body");
  body.addEventListener("click", (e) => {
    if (e.target.closest("textarea")) return;
    const tr = e.target.closest("tr[data-signal]");
    if (tr) showTrade(tr.dataset.signal);
  });
  body.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.tagName === "TR") showTrade(e.target.dataset.signal);
  });
  body.addEventListener("change", async (e) => {
    const ta = e.target.closest("textarea");
    if (!ta) return;
    try {
      const res = await api("/api/journal", { method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify({ key: ta.dataset.key, note: ta.value }) });
      if (!res.ok) throw new Error();
      state.journal[ta.dataset.key] = { note: ta.value };
      ta.title = "Zapisano";
    } catch {
      ta.setCustomValidity("Nie udało się zapisać notatki");
      ta.reportValidity();
      ta.setCustomValidity("");
    }
  });
  $$(".journal th button").forEach((btn) => btn.addEventListener("click", () => {
    const k = btn.dataset.sort;
    state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : 1 };
    renderJournal();
  }));
  $$("[data-filter]").forEach((cb) => cb.addEventListener("change", () => {
    state.filters[cb.dataset.filter] = cb.checked;
    renderJournal();
  }));
}

// ------------------------------------------------------------------ ustawienia

// Wartości domyślne: z config.py, a tick i wartość punktu z wybranego instrumentu.
function paramDefaults() {
  const inst = currentInstrument();
  return { ...state.config.defaults, TICK: inst?.tick ?? null, POINT_VALUE: inst?.point_value ?? null };
}

const PARAM_GROUPS = ["Instrument", "Poziomy", "Sygnały", "Ryzyko", "Okienko"];

function renderParams() {
  if (!state.config) return;
  const info = state.config.params;
  const defaults = paramDefaults();
  const inst = currentInstrument();
  const groups = {};
  for (const [name, meta] of Object.entries(info)) (groups[meta.group] ||= []).push([name, meta]);
  const current = { ...defaults, ...state.params };
  const show = (v, meta) => (meta.options ? meta.options[v]
    : typeof v === "boolean" ? (v ? "tak" : "nie") : String(v).replace(".", ","));

  $("#params-form").innerHTML = PARAM_GROUPS.filter((g) => groups[g]).map((g) => `
    <fieldset><legend>${g === "Instrument" && inst ? `Instrument: ${esc(inst.name)}` : g}</legend>
      ${groups[g].map(([name, meta]) => {
        const def = defaults[name];
        const val = current[name];
        const id = `p-${name}`;
        let input;
        if (meta.options) {
          input = `<select id="${id}" name="${name}" aria-describedby="${id}-h">${Object.entries(meta.options)
            .map(([k, label]) => `<option value="${esc(k)}" ${k === val ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
        } else if (typeof def === "boolean") {
          input = `<input type="checkbox" id="${id}" name="${name}" ${val ? "checked" : ""} aria-describedby="${id}-h">`;
        } else if (typeof def === "string") {
          input = `<input type="time" id="${id}" name="${name}" value="${esc(val)}" aria-describedby="${id}-h">`;
        } else {
          const step = name === "TICK" || name === "POINT_VALUE" ? "any" : Number.isInteger(def) ? 1 : 0.05;
          input = `<input type="number" id="${id}" name="${name}" value="${val}" step="${step}"
                     min="0" inputmode="decimal" aria-describedby="${id}-h">`;
        }
        const unit = name === "POINT_VALUE" && inst ? ` (${inst.currency})` : "";
        const showIf = meta.show_if ? ` data-show-if='${esc(JSON.stringify(meta.show_if))}'` : "";
        return `<div class="param ${meta.options ? "param--wide" : ""} ${val !== def ? "is-changed" : ""}"${showIf}>
          <label for="${id}">${meta.label}${unit}</label>${input}
          <small id="${id}-h">${meta.help} Domyślnie: ${esc(show(def, meta))}.</small>
        </div>`;
      }).join("")}
    </fieldset>`).join("");
  syncDependentFields();
}

// Pola zależne: np. „Ile minut po otwarciu” widać tylko przy trybie „Pierwsze minuty po otwarciu”.
function syncDependentFields() {
  const form = $("#params-form");
  form.querySelectorAll("[data-show-if]").forEach((row) => {
    const rule = JSON.parse(row.dataset.showIf);
    row.hidden = !Object.entries(rule).every(([field, value]) => form.elements[field]?.value === value);
  });
}

function readValue(el, def) {
  if (typeof def === "boolean") return el.checked;
  if (typeof def === "string") return el.value;
  return Number(String(el.value).replace(",", "."));
}

function readParams() {
  const defaults = paramDefaults();
  const out = {};
  for (const el of $("#params-form").elements) {
    if (!el.name) continue;
    const def = defaults[el.name];
    const v = readValue(el, def);
    if (typeof v === "number" && (Number.isNaN(v) || el.value === "")) continue;
    if (v !== def) out[el.name] = v;
  }
  return out;
}

function renderSettingsInfo() {
  const m = state.result.meta;
  $("#custom-badge").hidden = !m.custom_params;
  $("#settings-info").textContent = [m.trend_text, `Szukamy wejść ${m.window.label}`, ...m.warnings].join(" · ");
}

function initParams() {
  $("#params-form").addEventListener("input", (e) => {
    const el = e.target;
    el.closest(".param").classList.toggle("is-changed", readValue(el, paramDefaults()[el.name]) !== paramDefaults()[el.name]);
    syncDependentFields();
  });
  $("#reset-params").addEventListener("click", () => { state.params = {}; renderParams(); });
  $("#run-params").addEventListener("click", () => {
    state.params = readParams();
    state.showTab("signal");
    analyze();
  });
  $("#add-custom").addEventListener("click", openCustomDialog);
}

// ------------------------------------------------------------------ analiza

async function analyze() {
  if (!state.config) return connect();
  if (state.busy) return;   // nie wysyłamy dwóch zapytań naraz
  state.busy = true;
  $("#refresh").disabled = true;
  if (!state.result) setVerdictMessage("CZEKAJ", "Liczę poziomy i sygnały…");
  else $("#verdict-age").textContent = "Odświeżam…";

  try {
    const res = await api("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instrument: $("#symbol").value, ltf_interval: $("#interval").value, params: state.params }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `Serwer zwrócił błąd ${res.status}.`);
    setPriceDecimals(body.meta.instrument.price_decimals);
    state.result = body;
    state.selectedSignalId = null;
    renderVerdict();
    renderPlan();
    renderChartData();
    renderChart();
    renderBacktest();
    renderJournal();
    renderSettingsInfo();
    $("#export").href = `${API}/api/trades.csv?run_id=${body.run_id}`;
    api("/api/instruments").then((r) => r.json()).then((list) => setInstruments(list, $("#symbol").value)).catch(() => {});
    showLegendOnce();
  } catch (err) {
    // TypeError z fetch = serwer nie odpowiada; inaczej to czytelny błąd z API (np. brak danych).
    if (err instanceof TypeError) showOffline();
    else setVerdictMessage("BŁĄD", `Nie udało się przeprowadzić analizy. ${err.message}`);
  } finally {
    state.busy = false;
    $("#refresh").disabled = false;
  }
}

// ------------------------------------------------------------------ instrumenty

const CUSTOM_OPTION = "__custom";
const loadSaved = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
const save = (key, value) => { try { localStorage.setItem(key, value); } catch {} };

function currentInstrument() {
  return state.instruments.find((i) => i.id === $("#symbol").value);
}

// Lista wyboru pogrupowana (Indeksy, Waluty, Surowce, Krypto…); bez danych = wyłączone.
function setInstruments(list, preferredId) {
  state.instruments = list.instruments;
  const sel = $("#symbol");
  sel.innerHTML = list.groups.map((g) => {
    const items = list.instruments.filter((i) => i.group === g);
    if (!items.length) return "";
    return `<optgroup label="${esc(g)}">${items.map((i) =>
      `<option value="${esc(i.id)}" ${i.available ? "" : "disabled"}>${esc(i.name)}${
        !i.available ? " – niedostępny" : i.market_open ? " · otwarte" : " · zamknięte"}</option>`
    ).join("")}</optgroup>`;
  }).join("") + `<optgroup label="Inne"><option value="${CUSTOM_OPTION}">Własny symbol…</option></optgroup>`;

  const usable = list.instruments.filter((i) => i.available);
  const pick = usable.find((i) => i.id === preferredId) || usable[0];
  if (pick) sel.value = pick.id;
  sel.dataset.previous = sel.value;
}

function initInstruments() {
  const sel = $("#symbol");
  sel.addEventListener("change", () => {
    if (sel.value === CUSTOM_OPTION) {
      sel.value = sel.dataset.previous;     // lista wraca do poprzedniego, a otwieramy formularz
      openCustomDialog();
      return;
    }
    sel.dataset.previous = sel.value;
    save("gs-instrument", sel.value);
    // Tick i wartość punktu należą do instrumentu: przy zmianie wracają do wartości z rejestru.
    delete state.params.TICK;
    delete state.params.POINT_VALUE;
    renderParams();
    analyze();
  });
  $("#interval").addEventListener("change", analyze);
}

// ------------------------------------------------------------------ własny symbol

const customDialog = $("#custom-dialog");

function openCustomDialog() {
  if (!state.config) return showOffline();
  $("#custom-check-form").reset();
  $("#custom-form").hidden = true;
  $("#custom-status").textContent = "";
  customDialog.showModal();
  $("#custom-symbol").focus();
}

function initCustomDialog() {
  $("#custom-session-type").addEventListener("change", syncSessionFields);
  ["c-open", "c-close", "c-tz"].forEach((id) => $("#" + id).addEventListener("input", showPolishHours));

  // Krok 1: czy Yahoo ma dane dla tego symbolu?
  $("#custom-check-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const symbol = $("#custom-symbol").value.trim();
    const status = $("#custom-status");
    if (!symbol) return;
    status.className = "dialog__status";
    status.textContent = "Sprawdzam dane w Yahoo…";
    $("#custom-form").hidden = true;
    try {
      const res = await api(`/api/instruments/check?symbol=${encodeURIComponent(symbol)}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || "Nie udało się sprawdzić symbolu.");
      const last = new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 6 }).format(body.last_price);
      status.textContent = `Są dane: ${body.daily_bars} świec dziennych, ostatnia cena ${last}` +
        (body.intraday ? ", są też dane 5-minutowe." : ". Brak danych 5-minutowych – będą tylko poziomy, bez sygnałów.") +
        (body.existing ? " Masz już ten instrument – poniżej jego ustawienia; zapisanie zastąpi je." : "");
      fillCustomForm(body);
    } catch (err) {
      status.className = "dialog__status is-short";
      status.textContent = err.message;
    }
  });

  // Krok 2: zapisanie instrumentu z uzupełnionymi polami.
  $("#custom-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    const type = f.session_type.value;
    const payload = {
      symbol: $("#custom-symbol").value.trim(), name: f.name.value.trim(), group: f.group.value,
      tz: f.tz.value.trim(), currency: f.currency.value.trim(),
      session: type === "hours" ? `${f.open.value}-${f.close.value}` : type,
      tick: Number(f.tick.value.replace(",", ".")), point_value: Number(f.point_value.value.replace(",", ".")),
    };
    const status = $("#custom-status");
    try {
      const res = await api("/api/instruments", { method: "POST", headers: { "Content-Type": "application/json" },
                                                  body: JSON.stringify(payload) });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || "Nie udało się dodać instrumentu.");
      const list = await api("/api/instruments").then((r) => r.json());
      setInstruments(list, body.id);
      save("gs-instrument", body.id);
      customDialog.close();
      state.params = {};
      renderParams();
      state.showTab("signal");
      analyze();
    } catch (err) {
      status.className = "dialog__status is-short";
      status.textContent = err.message;
    }
  });
}

function fillCustomForm(info) {
  const f = $("#custom-form").elements;
  const ex = info.existing;   // instrument, który już masz: wczytujemy jego ustawienia do poprawy
  f.name.value = ex ? ex.name : info.symbol;
  f.group.value = ex ? ex.group : "Akcje";
  f.tz.value = ex ? ex.tz : info.tz || "";
  f.currency.value = ex ? ex.currency : info.currency || "";
  // Godziny: zapisane albo prawdziwe godziny tej giełdy (podpowiedź z serwera wg strefy czasowej).
  const session = ex ? ex.session : info.session || "09:00-17:00";
  const hours = /^\d\d:\d\d-\d\d:\d\d$/.test(session);
  f.session_type.value = hours ? "hours" : session;
  [f.open.value, f.close.value] = hours ? session.split("-") : ["09:00", "17:00"];
  // Tick zgadujemy z wielkości ceny (akcje za 135 zł → 0,01); użytkownik może go poprawić.
  const p = info.last_price;
  f.tick.value = ex ? ex.tick : p >= 1000 ? "0.1" : p >= 10 ? "0.01" : p >= 1 ? "0.001" : "0.0001";
  f.point_value.value = ex ? ex.point_value : "1";
  syncSessionFields();
  showPolishHours();
  $("#custom-form").hidden = false;
  f.name.focus();
}

// Przesunięcie strefy czasowej względem UTC (w minutach) w danym momencie – z Intl, bez bibliotek.
function tzOffsetMinutes(tz, date = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit" }).formatToParts(date).map((p) => [p.type, p.value]));
  const asUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute);
  return Math.round((asUtc - date.getTime()) / 60000);
}

// Pod godzinami sesji: ile to jest w czasie polskim (najczęstsza pomyłka: wpisanie godzin polskich).
function showPolishHours() {
  const f = $("#custom-form").elements;
  const out = $("#c-hours-pl");
  let text = "";
  try {
    const diff = tzOffsetMinutes("Europe/Warsaw") - tzOffsetMinutes(f.tz.value.trim());
    const shift = (hm) => {
      const [h, m] = hm.split(":").map(Number);
      const t = (((h * 60 + m + diff) % 1440) + 1440) % 1440;
      return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
    };
    if (f.open.value && f.close.value) {
      text = diff === 0 ? "Strefa giełdy = czas polski."
        : `Czyli ${shift(f.open.value)}–${shift(f.close.value)} czasu polskiego.`;
    }
  } catch {
    text = "Wpisz poprawną strefę czasową, np. America/New_York.";
  }
  out.textContent = text;
}

function syncSessionFields() {
  const hours = $("#custom-session-type").value === "hours";
  $$(".custom-hours").forEach((el) => { el.hidden = !hours; });
  $$(".custom-hours input").forEach((el) => { el.required = hours; });
}

// ------------------------------------------------------------------ legenda

// Jedna treść w dwóch miejscach: okno „?” przy wykresie i zakładka Ustawienia.
const LEGEND = [
  ["Skróty na wykresie", [
    ["D", "poziom z wykresu dziennego (mocniejszy)"],
    ["H1", "poziom z wykresu godzinowego"],
    ["PDC", "zamknięcie poprzedniego dnia"],
    ["PDH", "maksimum poprzedniego dnia"],
    ["PDL", "minimum poprzedniego dnia"],
    ["×N", "ile razy cena zawracała przy tym poziomie"],
    ['<span class="legend-swatch legend-swatch--zone" aria-label="pas"></span>',
     "pas między liniami: strefa, czyli kilka bliskich poziomów połączonych w jeden"],
    ["A", "wybicie (świeca zamyka się za strefą)"],
    ["B", "retest (powrót do wybitej strefy i odbicie)"],
    ["C", "grabienie płynności (knot za strefą i powrót)"],
    ["Wejście / SL / TP", "cena wejścia / stop loss / take profit"],
    ["R:R", "ile możesz zarobić na każdą złotówkę ryzyka"],
    ['<span class="legend-swatch legend-swatch--price">25 740</span>', "czarne pole na osi: bieżąca cena"],
    ['<span class="legend-swatch legend-swatch--cross">25 700</span>',
     "białe pole i przerywane linie: celownik (pozycja kursora myszy)"],
  ]],
  ["Stany na górze ekranu", [
    ["KUP / SPRZEDAJ", "jest aktywny sygnał, a dane są świeże: ustaw zlecenie stop z karty „Pokaż zlecenie”"],
    ["PLAN", "sygnał jest, ale dane są opóźnione: najpierw sprawdź warunek na wykresie u brokera (na żywo), dopiero potem ustaw zlecenie"],
    ["CZEKAJ", "nie ma sygnału; zdanie pod spodem mówi, na jaki warunek czekasz"],
    ["POZA OKIENKIEM", "giełda jest otwarta, ale wybrałeś szukanie wejść tylko w części sesji (Ustawienia → „Kiedy szukać wejść”); widać, kiedy jest następne okienko"],
    ["GIEŁDA ZAMKNIĘTA", "rynek teraz nie działa; widać, kiedy jest najbliższe otwarcie (święta giełdowe nie są uwzględnione)"],
  ]],
  ["Przyciski i polecenia", [
    ["Pokaż zlecenie", "karta z ceną aktywacji, SL, TP, wielkością i ważnością, do przepisania do platformy brokera"],
    ["Zlecenie stop kupna", "kupno uruchamia się dopiero, gdy cena wzrośnie do ceny aktywacji"],
    ["Zlecenie stop sprzedaży", "sprzedaż uruchamia się dopiero, gdy cena spadnie do ceny aktywacji"],
    ["Kopiuj", "kopiuje jedną wartość do schowka, żeby wkleić ją u brokera"],
    ["Wielkość", "ile jednostek (kontraktów CFD) kupić lub sprzedać, żeby ryzyko było takie jak w Ustawieniach"],
    ["↻", "pobiera świeże dane (same odświeżają się co 2 minuty)"],
    ["◐", "przełącza tryb jasny i ciemny"],
    ["Więcej", "włącza dodatki na wykresie: średnie, wolumen, sygnały z backtestu, odrzucone sygnały"],
    ["?", "otwiera tę legendę"],
    ["· otwarte / · zamknięte", "na liście instrumentów: czy ta giełda jest teraz otwarta"],
    ["Kiedy szukać wejść", "Ustawienia: cała sesja (domyślnie), tylko pierwsze minuty po otwarciu albo własne godziny"],
  ]],
  ["Plan na sesję", [
    ["Preferowany", "scenariusz zgodny z kierunkiem rynku; ten sprawdzaj w pierwszej kolejności"],
    ["Przeciw trendowi", "scenariusz w przeciwną stronę, tylko przy mocnej strefie D; ostrożnie"],
    ["Pominięty", "scenariusz, który nie spełnia zasad (np. za małe R:R); obok jest powód"],
    ["Luka", "otwarcie sesji daleko od zamknięcia poprzedniego dnia (PDC)"],
  ]],
];

function renderLegend() {
  const html = LEGEND.map(([title, rows]) =>
    `<h3>${title}</h3><dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`).join("");
  $$("[data-legend]").forEach((el) => { el.innerHTML = html; });
}

function initLegend() {
  renderLegend();
  $("#open-legend").addEventListener("click", () => $("#legend-dialog").showModal());
}

// Przy pierwszym uruchomieniu legenda otwiera się sama, jeden raz.
function showLegendOnce() {
  if (loadSaved("gs-legend-seen")) return;
  save("gs-legend-seen", "1");
  $("#legend-dialog").showModal();
}

// ------------------------------------------------------------------ plan na sesję

function renderPlan() {
  const plan = state.result.session_plan;
  const box = $("#plan");
  box.hidden = !plan || !plan.lines.length;
  if (box.hidden) return;
  $("#plan-title").textContent = plan.title;
  $("#plan-lines").innerHTML = plan.lines.map((l) => `<li class="is-${l.kind}">${esc(l.text)}</li>`).join("");
}

// ------------------------------------------------------------------ motyw

function initTheme() {
  $("#theme").addEventListener("click", () => {
    const root = document.documentElement;
    const isDark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = isDark ? "light" : "dark";
    save("gs-theme", root.dataset.theme);
    applyChartTheme();
    applyEquityTheme();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { applyChartTheme(); applyEquityTheme(); });
}

// ------------------------------------------------------------------ start

function init() {
  initTabs();
  initChart();
  initTheme();
  initOrderCard();
  initJournal();
  initParams();
  initInstruments();
  initCustomDialog();
  initLegend();
  initResults();

  $$("[data-toggle]").forEach((cb) => cb.addEventListener("change", () => {
    state.toggles[cb.dataset.toggle] = cb.checked;
    if (state.result) renderChart();
  }));
  $("#refresh").addEventListener("click", analyze);

  // Dane się starzeją: co 2 minuty odświeżamy, gdy karta przeglądarki jest widoczna.
  setInterval(() => {
    if (state.config && !state.busy && document.visibilityState === "visible" && !state.selectedSignalId) analyze();
  }, 120000);

  connect();
}

init();
