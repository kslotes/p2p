"use strict";

// Live monitor page: SSE from /api/stream, polling /api/snapshots as fallback.
// History tab: /api/orders with filters, sync status from /api/orders/sync.
// Every value from the server is rendered with textContent (ad and order text
// is untrusted).

const SSE_RETRY_MS = 30000;
const DEFAULT_POLL_MS = 15000;
// Sellers and buyers sit side by side, so only the first payment method fits;
// the full list is in the hover tooltip.
const MAX_PAYMENT_METHODS_SHOWN = 1;
const FEE_STORAGE_KEY = "p2p.makerFeePct";
const DEFAULT_FEE = "0.20";
const DEFAULT_FIAT = "ARS";

const money = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const quantity = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 });

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

// ---------- formatting (display only; the API keeps exact decimal strings)

const fmtMoney = (text) => (text == null ? "-" : money.format(Number(text)));
const fmtQuantity = (text) => quantity.format(Number(text));
const fmtPercent = (ratioText) => `${(Number(ratioText) * 100).toFixed(1)}%`;
const fmtTime = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "never";

function fmtCompact(text) {
  // Short fiat amount for limits: 1000 -> 1K, 9800000 -> 9.8M (same as the CLI).
  const value = Number(text);
  for (const [threshold, suffix] of [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ]) {
    if (Math.abs(value) >= threshold) {
      return `${Number((value / threshold).toFixed(2))}${suffix}`;
    }
  }
  return String(Number(value.toFixed(2)));
}

function fmtMethods(methods) {
  const shown = methods.slice(0, MAX_PAYMENT_METHODS_SHOWN);
  const extra = methods.length - shown.length;
  return shown.join(", ") + (extra > 0 ? ` +${extra}` : "");
}

// ---------- asset panels

// Tick after the nick of Binance verified merchants; the tooltip names the kind
// (MASS_MERCHANT -> MASS, BLOCK_MERCHANT -> BLOCK) when Binance reports it.
function advertiserCell(advertiser) {
  const cell = el("td", {}, advertiser.nick);
  if (advertiser.verified) {
    const kind = advertiser.merchant_identity?.replace(/_MERCHANT$/, "");
    cell.append(
      " ",
      el(
        "span",
        {
          class: "verified",
          role: "img",
          "aria-label": "Verified merchant",
          title: kind ? `Verified merchant (${kind})` : "Verified merchant",
        },
        "✓",
      ),
    );
  }
  return cell;
}

function methodsCell(methods) {
  return el(
    "td",
    { class: "methods-compact", title: methods.join(", ") },
    fmtMethods(methods),
  );
}

// Implied fiat per USDT of a BTC/ETH price; the API sends null for stablecoins
// and while no spot price is known, and el() skips the null child.
function usdtRate(rate, fiat) {
  if (rate == null) return null;
  return el(
    "span",
    { class: "usdt-rate", title: "Price / Binance spot price in USDT" },
    `(≈ ${fmtMoney(rate)} ${fiat}/USDT)`,
  );
}

// Binance spot price behind the implied rates; null for stablecoins and while
// no spot price is known, so el() skips it.
function spotPrice(price, asset) {
  if (price == null) return null;
  return el(
    "span",
    { class: "spot-price", title: `Binance spot price of ${asset} in USDT` },
    `spot ${fmtMoney(price)} USDT`,
  );
}

function adsTable(title, ads, fiat, sideClass) {
  const head = el(
    "tr",
    {},
    el("th", { class: "num" }, `Price ${fiat}`),
    el("th", { class: "num" }, "Available"),
    el("th", { class: "num" }, `Limits ${fiat}`),
    el("th", {}, "Pay"),
    el("th", {}, "Advertiser"),
    el("th", { class: "num" }, "Orders 30d"),
  );
  const rows = ads.length
    ? ads.map((ad) =>
        el(
          "tr",
          {},
          el(
            "td",
            { class: `num ${sideClass}` },
            fmtMoney(ad.price),
            usdtRate(ad.usdt_rate, fiat),
          ),
          el("td", { class: "num" }, fmtQuantity(ad.available)),
          el(
            "td",
            { class: "num" },
            `${fmtCompact(ad.min_amount)}-${fmtCompact(ad.max_amount)}`,
          ),
          methodsCell(ad.payment_methods),
          advertiserCell(ad.advertiser),
          el(
            "td",
            { class: "num" },
            `${ad.advertiser.month_orders} @ ${fmtPercent(ad.advertiser.finish_rate)}`,
          ),
        ),
      )
    : [el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No ads"))];
  return el(
    "div",
    { class: "side" },
    el("h3", { class: sideClass }, title),
    el(
      "div",
      { class: "table-wrap" },
      el("table", {}, el("thead", {}, head), el("tbody", {}, ...rows)),
    ),
  );
}

function assetPanel(entry) {
  const hasData = entry.updated_at !== null;
  const title = el(
    "h2",
    {},
    `${entry.asset}/${entry.fiat}`,
    spotPrice(entry.spot_price, entry.asset),
    el(
      "span",
      { class: "meta" },
      hasData ? `data from ${fmtTime(entry.updated_at)}` : "",
    ),
  );
  const panel = el("section", { class: "panel" }, title);

  if (hasData) {
    const spread = entry.spread
      ? `${fmtMoney(entry.spread.absolute)} (${Number(entry.spread.percentage).toFixed(2)}%)`
      : "-";
    panel.append(
      el(
        "div",
        { class: "summary" },
        el(
          "span",
          {},
          "Best buy price (lowest ask): ",
          el("strong", { class: "buy" }, fmtMoney(entry.best_buy)),
          usdtRate(entry.best_buy_usdt_rate, entry.fiat),
        ),
        el(
          "span",
          {},
          "Best sell price (highest bid): ",
          el("strong", { class: "sell" }, fmtMoney(entry.best_sell)),
          usdtRate(entry.best_sell_usdt_rate, entry.fiat),
        ),
        el("span", {}, "Spread: ", el("strong", {}, spread)),
      ),
    );
    const sides = [
      adsTable("Sellers (you buy from them)", entry.asks, entry.fiat, "buy"),
      adsTable("Buyers (you sell to them)", entry.bids, entry.fiat, "sell"),
    ];
    panel.append(el("div", { class: "sides" }, ...sides));
  }
  if (entry.error) {
    const stale = hasData ? " (showing last good data)" : "";
    panel.append(el("p", { class: "error" }, `Refresh error${stale}: ${entry.error}`));
  }
  if (!hasData && !entry.error) {
    panel.append(el("p", { class: "muted" }, "Waiting for data..."));
  }
  return panel;
}

function render(payload) {
  $("panels").replaceChildren(...payload.assets.map(assetPanel));
  $("updated").textContent = fmtTime(payload.updated_at);
  const settings = [`refresh every ${payload.interval}s`];
  if (payload.pay_types.length) settings.push(`pay types: ${payload.pay_types.join(", ")}`);
  if (payload.spot_error) settings.push(`spot price error: ${payload.spot_error}`);
  $("settings").textContent = `| ${settings.join(" | ")}`;
  pollMs = payload.interval * 1000 || DEFAULT_POLL_MS;
  calcFiat = payload.fiat || DEFAULT_FIAT;
}

// ---------- live updates: SSE first, polling while SSE is down

let source = null;
let pollTimer = null;
let pollGeneration = 0; // bumped by stopPolling so in-flight ticks do not re-arm
let pollMs = DEFAULT_POLL_MS;

function setConnection(state, label) {
  $("connection").dataset.state = state;
  $("connection-label").textContent = label;
}

async function pollOnce() {
  try {
    const response = await fetch("/api/snapshots", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
    setConnection("polling", "Polling (live stream unavailable)");
  } catch {
    // Keep the last rendered data; only the connection badge changes.
    setConnection("offline", "Server unreachable, showing last data");
  }
}

function startPolling() {
  if (pollTimer !== null) return;
  const generation = pollGeneration;
  const tick = async () => {
    await pollOnce();
    if (generation !== pollGeneration) return; // stopped while fetching
    pollTimer = setTimeout(tick, pollMs);
  };
  pollTimer = setTimeout(tick, 0);
}

function stopPolling() {
  pollGeneration += 1;
  clearTimeout(pollTimer);
  pollTimer = null;
}

function connectStream() {
  if (!("EventSource" in window)) {
    startPolling();
    return;
  }
  source = new EventSource("/api/stream");
  source.onmessage = (event) => {
    stopPolling();
    setConnection("live", "Live");
    render(JSON.parse(event.data));
  };
  source.onerror = () => {
    source.close();
    source = null;
    startPolling();
    setTimeout(connectStream, SSE_RETRY_MS);
  };
}

// ---------- round-trip calculator

function readStoredFee() {
  try {
    return localStorage.getItem(FEE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeFee(value) {
  try {
    localStorage.setItem(FEE_STORAGE_KEY, value);
  } catch {
    // Storage blocked (private mode etc.): the fee just is not remembered.
  }
}

// Accept "1,234.56", "1.234,56" and "1234,56": when both separators appear the
// last one is the decimal point; a lone comma is the decimal point; repeated
// commas are thousands separators.
function normalize(text) {
  const value = text.trim();
  const comma = value.lastIndexOf(",");
  const dot = value.lastIndexOf(".");
  if (comma !== -1 && dot !== -1) {
    return comma > dot
      ? value.replaceAll(".", "").replace(",", ".")
      : value.replaceAll(",", "");
  }
  if (value.indexOf(",") !== comma) return value.replaceAll(",", "");
  return value.replace(",", ".");
}

let calcTimer = null;
let calcController = null;
let calcFiat = DEFAULT_FIAT; // fiat of the monitored pairs, for the estimate label

const signed = (text) => `${Number(text) > 0 ? "+" : ""}${fmtMoney(text)}`;

function showResult(result) {
  $("real-sell").textContent = result ? fmtMoney(result.real_sell) : "-";
  $("real-rebuy").textContent = result ? fmtMoney(result.real_rebuy) : "-";
  if (result) {
    const sign = Number(result.difference) > 0 ? "+" : "";
    $("difference").textContent =
      `${sign}${fmtMoney(result.difference)} (${sign}${Number(result.difference_pct).toFixed(2)}%)`;
  } else {
    $("difference").textContent = "-";
  }
  const estimate = $("estimate");
  estimate.dataset.outcome = result ? result.outcome : "";
  if (result) {
    // fiat and asset on separate lines so the narrow widget never wraps mid-amount
    const fiatLine = document.createElement("span");
    fiatLine.className = "estimate-line";
    fiatLine.textContent = `${signed(result.fiat_result)} ${calcFiat}`;
    const assetLine = document.createElement("span");
    assetLine.className = "estimate-line";
    assetLine.textContent = `≈ ${signed(result.asset_result)} USDT`;
    estimate.replaceChildren(fiatLine, assetLine);
  } else {
    estimate.textContent = "-";
  }
  const badge = $("outcome");
  badge.dataset.outcome = result ? result.outcome : "";
  badge.textContent = result ? result.outcome.replace("_", "-") : "-";
}

async function calculate() {
  const sell = normalize($("sell").value);
  const rebuy = normalize($("rebuy").value);
  const fee = normalize($("fee").value);
  $("calc-error").textContent = "";
  // Abort first so a stale in-flight response cannot repaint a cleared result.
  calcController?.abort();
  calcController = null;
  if (!sell || !rebuy || !fee) {
    showResult(null);
    return;
  }
  calcController = new AbortController();
  const query = new URLSearchParams({ sell, rebuy, fee_pct: fee });
  try {
    const response = await fetch(`/api/round-trip?${query}`, {
      signal: calcController.signal,
    });
    if (response.status === 422) {
      // Domain rejections carry a readable string; query validation a list.
      const { detail } = await response.json().catch(() => ({}));
      showResult(null);
      $("calc-error").textContent =
        typeof detail === "string"
          ? `Invalid input: ${detail}.`
          : "Prices must be > 0 and the fee in [0, 100).";
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    showResult(await response.json());
  } catch (error) {
    if (error.name === "AbortError") return;
    showResult(null);
    $("calc-error").textContent = "Calculator unavailable (server unreachable).";
  }
}

function scheduleCalculation() {
  clearTimeout(calcTimer);
  calcTimer = setTimeout(calculate, 300);
}

// Default fee from the user's synced orders. Only applies while the user never
// entered a fee (nothing stored) and has not touched the field meanwhile.
async function applyRealFee() {
  try {
    const response = await fetch("/api/orders/maker-fee", { cache: "no-store" });
    if (!response.ok) return;
    const { maker_fee_pct: real } = await response.json();
    if (real == null || readStoredFee() !== null || $("fee").value !== DEFAULT_FEE) {
      return;
    }
    $("fee").value = real;
    $("fee-hint").hidden = false;
    scheduleCalculation();
  } catch {
    // Keep the 0.20% default.
  }
}

function initCalculator() {
  const storedFee = readStoredFee();
  $("fee").value = storedFee ?? DEFAULT_FEE;
  if (storedFee === null) applyRealFee();
  $("calc").addEventListener("input", (event) => {
    if (event.target.id === "fee") {
      storeFee(event.target.value); // a user-entered fee always wins
      $("fee-hint").hidden = true;
    }
    scheduleCalculation();
  });
  $("calc").addEventListener("submit", (event) => event.preventDefault());
}

// ---------- tabs (monitor / history), remembered in the browser

const TAB_STORAGE_KEY = "p2p.tab";
const TABS = ["monitor", "history"];

function readStoredTab() {
  try {
    return localStorage.getItem(TAB_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeTab(name) {
  try {
    localStorage.setItem(TAB_STORAGE_KEY, name);
  } catch {
    // Storage blocked: the page just opens on the monitor next time.
  }
}

function showTab(name) {
  for (const tab of TABS) {
    const active = tab === name;
    $(`tab-${tab}`).setAttribute("aria-selected", String(active));
    $(`view-${tab}`).hidden = !active;
  }
  if (name === "history") startHistory();
  else stopHistory();
}

function initTabs() {
  for (const tab of TABS) {
    $(`tab-${tab}`).addEventListener("click", () => {
      storeTab(tab);
      showTab(tab);
    });
  }
  const stored = readStoredTab();
  showTab(TABS.includes(stored) ? stored : "monitor");
}

// ---------- order history

const SYNC_POLL_MS = 3000; // while a sync runs
const SYNC_IDLE_POLL_MS = 60000; // otherwise, to pick up background syncs

const fmtDateTime = (iso) =>
  new Date(iso).toLocaleString([], {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
const fmtLabel = (value) => value.toLowerCase().replaceAll("_", " ");

let historyTimer = null;
let historyActive = false;
let historyController = null;
let lastSyncSeen = null; // last_success already reflected in the table

function historyQuery() {
  const query = new URLSearchParams();
  for (const [key, id] of [
    ["asset", "filter-asset"],
    ["side", "filter-side"],
    ["status", "filter-status"],
    ["from", "filter-from"],
    ["to", "filter-to"],
  ]) {
    const value = $(id).value;
    if (value) query.set(key, value);
  }
  return query;
}

function renderSyncStatus(sync) {
  const line = $("sync-status");
  const button = $("sync-now");
  $("history-setup").hidden = sync.configured;
  button.hidden = !sync.configured;
  button.disabled = sync.running;
  if (!sync.configured) {
    line.textContent = "Order sync not configured.";
    return;
  }
  const parts = [];
  if (sync.running) parts.push("Syncing with Binance...");
  parts.push(
    sync.last_success
      ? `Last sync ${fmtDateTime(sync.last_success)}`
      : "Not synced yet",
  );
  if (sync.interval_seconds) {
    parts.push(`every ${Number((sync.interval_seconds / 60).toFixed(1))} min`);
  } else {
    parts.push("automatic sync off");
  }
  if (sync.last_error) parts.push(`last attempt failed: ${sync.last_error}`);
  line.textContent = parts.join(" | ");
}

function renderAssetOptions(assets) {
  const select = $("filter-asset");
  const current = select.value;
  const options = [el("option", { value: "" }, "All")];
  for (const asset of assets) options.push(el("option", { value: asset }, asset));
  select.replaceChildren(...options);
  select.value = assets.includes(current) ? current : "";
}

function renderTotals(totals) {
  const rows = totals.groups.length
    ? totals.groups.map((group) =>
        el(
          "tr",
          {},
          el("td", { class: group.side === "BUY" ? "buy" : "sell" }, group.side),
          el("td", {}, group.asset),
          el("td", { class: "num" }, String(group.count)),
          el("td", { class: "num" }, `${fmtQuantity(group.amount)} ${group.asset}`),
          el("td", { class: "num" }, `${fmtMoney(group.total_price)} ${group.fiat}`),
          el("td", { class: "num" }, `${fmtQuantity(group.fees)} ${group.asset}`),
        ),
      )
    : [el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No completed orders"))];
  $("history-totals").replaceChildren(...rows);
}

function renderOrders(orders) {
  $("history-count").textContent = `Orders (${orders.length})`;
  const rows = orders.length
    ? orders.map((order) =>
        el(
          "tr",
          {},
          el("td", {}, fmtDateTime(order.created_at)),
          el("td", { class: order.side === "BUY" ? "buy" : "sell" }, order.side),
          el("td", {}, order.asset),
          el("td", { class: "num" }, fmtQuantity(order.amount)),
          el("td", { class: "num" }, fmtMoney(order.unit_price)),
          el("td", { class: "num" }, `${fmtMoney(order.total_price)} ${order.fiat}`),
          el("td", { class: "num" }, order.fee == null ? "-" : fmtQuantity(order.fee)),
          el("td", {}, fmtLabel(order.role)),
          el("td", { "data-status": order.status }, fmtLabel(order.status)),
          el("td", {}, order.counterparty),
          el("td", {}, order.pay_method),
        ),
      )
    : [el("tr", {}, el("td", { colspan: "11", class: "muted" }, "No orders"))];
  $("history-rows").replaceChildren(...rows);
}

function showHistoryError(message) {
  const box = $("history-error");
  box.textContent = message;
  box.hidden = !message;
}

async function loadHistory() {
  historyController?.abort();
  historyController = new AbortController();
  try {
    const response = await fetch(`/api/orders?${historyQuery()}`, {
      cache: "no-store",
      signal: historyController.signal,
    });
    // Never show the server's validation detail: it may echo stored data.
    if (response.status === 422) {
      showHistoryError("Invalid filter: check the dates (From must not be after To).");
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    showHistoryError("");
    renderAssetOptions(body.assets);
    renderTotals(body.totals);
    renderOrders(body.orders);
    renderSyncStatus(body.sync);
    lastSyncSeen = body.sync.last_success;
  } catch (error) {
    if (error.name === "AbortError") return;
    showHistoryError("Order history unavailable (server unreachable).");
  }
}

async function pollSyncStatus() {
  let running = false;
  try {
    const response = await fetch("/api/orders/sync", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const sync = await response.json();
    running = sync.running;
    renderSyncStatus(sync);
    if (sync.last_success !== lastSyncSeen) await loadHistory();
  } catch {
    // Keep the last rendered history; the next tick retries.
  }
  scheduleSyncPoll(running ? SYNC_POLL_MS : SYNC_IDLE_POLL_MS);
}

function scheduleSyncPoll(delay) {
  clearTimeout(historyTimer);
  historyTimer = historyActive ? setTimeout(pollSyncStatus, delay) : null;
}

async function syncNow() {
  $("sync-now").disabled = true;
  try {
    const response = await fetch("/api/orders/sync", { method: "POST" });
    if (response.ok) renderSyncStatus(await response.json());
  } catch {
    // The status poll below reports the outcome.
  }
  scheduleSyncPoll(SYNC_POLL_MS);
}

function startHistory() {
  if (historyActive) return;
  historyActive = true;
  loadHistory();
  scheduleSyncPoll(SYNC_POLL_MS);
}

function stopHistory() {
  historyActive = false;
  scheduleSyncPoll(0);
}

function initHistory() {
  $("history-filters").addEventListener("change", loadHistory);
  $("history-filters").addEventListener("submit", (event) => event.preventDefault());
  $("sync-now").addEventListener("click", syncNow);
}

initCalculator();
initHistory();
initTabs();
connectStream();
