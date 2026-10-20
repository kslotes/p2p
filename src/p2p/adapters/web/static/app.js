"use strict";

// Live monitor page: SSE from /api/stream, polling /api/snapshots as fallback.
// Every value from the server is rendered with textContent (ad text is untrusted).

const SSE_RETRY_MS = 30000;
const DEFAULT_POLL_MS = 15000;
const MAX_PAYMENT_METHODS_SHOWN = 3;
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

function adsTable(title, ads, fiat, sideClass) {
  const head = el(
    "tr",
    {},
    el("th", { class: "num" }, `Price ${fiat}`),
    el("th", { class: "num" }, "Available"),
    el("th", { class: "num" }, `Limits ${fiat}`),
    el("th", {}, "Payment methods"),
    el("th", {}, "Advertiser"),
    el("th", { class: "num" }, "Orders 30d"),
  );
  const rows = ads.length
    ? ads.map((ad) =>
        el(
          "tr",
          {},
          el("td", { class: `num ${sideClass}` }, fmtMoney(ad.price)),
          el("td", { class: "num" }, fmtQuantity(ad.available)),
          el(
            "td",
            { class: "num" },
            `${fmtCompact(ad.min_amount)}-${fmtCompact(ad.max_amount)}`,
          ),
          el("td", { class: "wrap" }, fmtMethods(ad.payment_methods)),
          advertiserCell(ad.advertiser),
          el(
            "td",
            { class: "num" },
            `${ad.advertiser.month_orders} @ ${fmtPercent(ad.advertiser.finish_rate)}`,
          ),
        ),
      )
    : [el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No ads"))];
  return [
    el("h3", { class: sideClass }, title),
    el(
      "div",
      { class: "table-wrap" },
      el("table", {}, el("thead", {}, head), el("tbody", {}, ...rows)),
    ),
  ];
}

function assetPanel(entry) {
  const hasData = entry.updated_at !== null;
  const title = el(
    "h2",
    {},
    `${entry.asset}/${entry.fiat}`,
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
        ),
        el(
          "span",
          {},
          "Best sell price (highest bid): ",
          el("strong", { class: "sell" }, fmtMoney(entry.best_sell)),
        ),
        el("span", {}, "Spread: ", el("strong", {}, spread)),
      ),
      ...adsTable("Sellers (you buy from them)", entry.asks, entry.fiat, "buy"),
      ...adsTable("Buyers (you sell to them)", entry.bids, entry.fiat, "sell"),
    );
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
  estimate.textContent = result
    ? `${signed(result.fiat_result)} ${calcFiat} (≈ ${signed(result.asset_result)} USDT)`
    : "-";
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

function initCalculator() {
  $("fee").value = readStoredFee() ?? DEFAULT_FEE;
  $("calc").addEventListener("input", (event) => {
    if (event.target.id === "fee") storeFee(event.target.value);
    scheduleCalculation();
  });
  $("calc").addEventListener("submit", (event) => event.preventDefault());
}

initCalculator();
connectStream();
