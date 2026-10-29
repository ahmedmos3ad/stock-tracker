"""ThndrX-style "Return Analytics" card, rendered as a self-contained HTML/JS component.

Streamlit's native charts can't update a headline number while you hover, so the whole card
(Returns/Equity toggle, range pills, scrubber, tooltip) lives client-side in one iframe: hovering
and switching ranges is instant and never reruns the app.

  * Returns view -> portfolio value minus net cash invested. The headline is the change from the
    start of the selected range to the hovered day (like Thndr).
  * Equity view  -> portfolio value (green) over net deposits (purple), plus all-time returns.
"""

from __future__ import annotations

import json
from bisect import bisect_right

import pandas as pd

HEIGHT = 560


def card_html(
    df: pd.DataFrame,
    last_updated: str,
    ccy: str = "EGP",
    index_history: list[tuple[str, float]] | None = None,
    default_range: str = "1M",
) -> str:
    """``df``: daily rows indexed by datetime with ``value`` and ``contributed`` columns.

    ``index_history``: [(iso date, close)] of the benchmark (EGX30). Each day also gets ``g``, the
    portfolio's chained time-weighted growth factor (deposits/withdrawals don't count as gains),
    and ``x``, the index level (carried forward over non-trading days), so the card can show
    "you vs the index" for any range and hovered day.
    """
    idx = sorted(index_history or [])
    idx_dates = [d for d, _ in idx]
    points = []
    g, prev_v, prev_c = 1.0, 0.0, 0.0
    for ts, r in df.iterrows():
        v, c = float(r["value"]), float(r["contributed"])
        if prev_v > 0:
            g *= (v - (c - prev_c)) / prev_v
        prev_v, prev_c = v, c
        iso = ts.strftime("%Y-%m-%d")
        k = bisect_right(idx_dates, iso) - 1
        points.append({
            "d": iso, "v": round(v, 2), "c": round(c, 2), "g": round(g, 6),
            "x": idx[k][1] if k >= 0 else None,
        })
    return (
        _TEMPLATE.replace("__DATA__", json.dumps(points))
        .replace("__UPDATED__", json.dumps(last_updated))
        .replace("__CCY__", json.dumps(ccy))
        .replace("__RANGE__", json.dumps(default_range))
        .replace("__HASIDX__", "true" if len(idx) >= 2 else "false")
    )


_TEMPLATE = r"""
<style>
  html,body{margin:0;background:transparent;color:#e6e6e6;font-family:"Source Sans Pro",system-ui,sans-serif}
  .card{padding:4px 2px;user-select:none}
  .top{display:flex;justify-content:space-between;align-items:center}
  .title{font-size:1.05rem;font-weight:600}
  .seg button,.pills button{background:transparent;border:1px solid #2b2f36;color:#8a8f98;cursor:pointer;
    font:inherit;font-size:.9rem;padding:6px 12px}
  .seg button{border-radius:2px}
  .seg button.on{background:#2a2d33;color:#fff;border-color:#4a4f5c}
  .updated{color:#8a8f98;font-size:.85rem;margin:14px 0 14px}
  .head{display:flex;justify-content:space-between;align-items:flex-end;min-height:62px}
  .lbl{color:#8a8f98;font-size:.9rem;margin-bottom:2px}
  .big{font-size:2rem;line-height:1.15;font-weight:500;color:#fff}
  .side{display:flex;gap:0;font-size:.95rem}
  .side>div{padding:0 14px;text-align:right}
  .side>div+div{border-left:1px solid #2b2f36;padding-right:0}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle}
  .side b{display:block;font-weight:500;margin-top:6px;color:#fff}
  .cmp{display:flex;gap:22px;flex-wrap:wrap;margin-top:12px;font-size:.95rem;color:#8a8f98}
  .cmp b{font-weight:600;margin-left:6px}
  .up{color:#7ed957}.down{color:#ff4d6a}
  #plot{position:relative;margin-top:10px}
  svg{display:block;width:100%;overflow:visible}
  #tip{position:absolute;pointer-events:none;background:#2a2d33;border-radius:6px;padding:10px 14px;
    font-size:.85rem;display:none;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.5)}
  #tip .t{color:#9aa0a6;margin-bottom:4px}
  #tip b{color:#fff}
  #tip .row{display:flex;gap:10px;justify-content:space-between}
  .pills{display:flex;justify-content:center;gap:10px;margin-top:18px}
  .pills button{border:1px solid transparent;color:#fff;font-weight:600;border-radius:4px;padding:7px 12px}
  .pills button.on{background:#2a2d33}
</style>
<div class="card">
  <div class="top">
    <div class="title">Return Analytics</div>
    <div class="seg"><button id="vReturns" class="on">Returns EGP</button><button id="vEquity">Equity EGP</button></div>
  </div>
  <div class="updated" id="updated"></div>
  <div class="head">
    <div><div class="lbl" id="lbl"></div><div class="big" id="big"></div></div>
    <div class="side" id="side" style="display:none">
      <div><span class="dot" style="background:#7ed957"></span><span class="lbl">Portfolio Value</span><b id="sv"></b></div>
      <div><span class="dot" style="background:#8c8cff"></span><span class="lbl">Net Deposits</span><b id="sc"></b></div>
    </div>
  </div>
  <div class="cmp" id="cmp"></div>
  <div id="plot"><svg id="svg"></svg><div id="tip"></div></div>
  <div class="pills" id="pills"></div>
</div>
<script>
const D = __DATA__.map(p => ({...p, t: Date.parse(p.d + "T00:00:00Z"), r: p.v - p.c}));
const HAS_IDX = __HASIDX__, CCY = __CCY__, GREEN = "#7ed957", RED = "#ff4d6a", PURPLE = "#8c8cff", BG = "#0e1117";
const RANGES = ["1D", "1M", "6M", "1Y", "YTD", "All"];
let view = "returns", range = __RANGE__, hover = null, pts = [];
const H = 300, M = {l: 46, r: 6, t: 8, b: 8};
const $ = id => document.getElementById(id);
$("updated").textContent = "Last Updated: " + fmtDate(Date.parse(__UPDATED__ + "T00:00:00Z"));

function fmtDate(t) {
  return new Date(t).toLocaleDateString("en-GB", {day: "2-digit", month: "short", year: "numeric", timeZone: "UTC"});
}
function money(n) { return n.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " " + CCY; }
function kfmt(n) {
  const a = Math.abs(n);
  return a >= 1e6 ? (n / 1e6).toFixed(1) + "M" : a >= 1e3 ? Math.round(n / 1e3) + "K" : String(Math.round(n));
}
function niceTicks(lo, hi, n) {
  const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag, out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}
function sliceRange() {
  const last = D[D.length - 1], day = 86400000;
  let cut = 0;
  if (range === "1D") cut = last.t - day;
  else if (range === "1M") cut = last.t - 30 * day;
  else if (range === "6M") cut = last.t - 182 * day;
  else if (range === "1Y") cut = last.t - 365 * day;
  else if (range === "YTD") cut = Date.UTC(new Date(last.t).getUTCFullYear(), 0, 1);
  let out = D.filter(p => p.t >= cut);
  if (out.length < 2) out = D.slice(-2);
  return out;
}
// Monotone cubic (Fritsch-Carlson): smooth like Thndr but never overshoots a flat stretch.
function smoothPath(xy) {
  const n = xy.length;
  if (n < 2) return "";
  const dx = [], m = [], t = new Array(n);
  for (let i = 0; i < n - 1; i++) { dx.push(xy[i + 1][0] - xy[i][0]); m.push((xy[i + 1][1] - xy[i][1]) / (dx[i] || 1)); }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = "M" + xy[0][0] + "," + xy[0][1];
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += "C" + (xy[i][0] + h) + "," + (xy[i][1] + t[i] * h) + " " + (xy[i + 1][0] - h) + "," + (xy[i + 1][1] - t[i + 1] * h) +
         " " + xy[i + 1][0] + "," + xy[i + 1][1];
  }
  return d;
}
function pct(x) { return (x >= 0 ? "+" : "\u2212") + Math.abs(x * 100).toFixed(2) + "%"; }
function cmpHtml(label, x) { return `<span>${label}<b class="${x >= 0 ? "up" : "down"}">${pct(x)}</b></span>`; }
function setCompare(p, first) {
  const me = p.g / first.g - 1;
  let h = cmpHtml("You", me);
  if (p.x != null && first.x != null && HAS_IDX) {
    const ix = p.x / first.x - 1, diff = (me - ix) * 100;
    h += cmpHtml("EGX30", ix) +
         `<span>vs EGX30<b class="${diff >= 0 ? "up" : "down"}">${(diff >= 0 ? "+" : "\u2212") + Math.abs(diff).toFixed(2)} pp</b></span>`;
  } else {
    h += `<span>EGX30: enter its level in the Prices tab on 2+ different days to compare</span>`;
  }
  $("cmp").innerHTML = h;
}
function setHeader() {
  const i = hover === null ? pts.length - 1 : hover, p = pts[i], first = pts[0];
  setCompare(p, first);
  if (view === "returns") {
    $("lbl").textContent = "Returns Value"; $("big").textContent = money(p.r - first.r);
    $("side").style.display = "none";
  } else {
    $("lbl").textContent = "All-Time Returns"; $("big").textContent = money(p.r);
    $("side").style.display = "flex"; $("sv").textContent = money(p.v); $("sc").textContent = money(p.c);
  }
}
function render() {
  pts = sliceRange();
  const W = $("plot").clientWidth, pw = W - M.l - M.r, ph = H - M.t - M.b;
  const series = view === "returns"
    ? [{k: "r", color: (pts[pts.length - 1].r - pts[0].r) >= 0 ? GREEN : RED}]
    : [{k: "v", color: GREEN}, {k: "c", color: PURPLE}];
  let lo = Infinity, hi = -Infinity;
  for (const s of series) for (const p of pts) { lo = Math.min(lo, p[s.k]); hi = Math.max(hi, p[s.k]); }
  const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.05 || 1;
  lo -= pad; hi += pad;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const X = t => M.l + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * pw;
  const Y = v => M.t + (1 - (v - lo) / (hi - lo)) * ph;
  let g = "<defs>";
  series.forEach((s, i) => {
    g += `<linearGradient id="g${i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity=".28"/>` +
         `<stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient>`;
  });
  g += "</defs>";
  for (const v of niceTicks(lo, hi, 4)) {
    const y = Y(v);
    g += `<line x1="${M.l}" x2="${W - M.r}" y1="${y}" y2="${y}" stroke="#262a31"/>` +
         `<text x="${M.l - 8}" y="${y + 4}" text-anchor="end" fill="#8a8f98" font-size="12">${kfmt(v)}</text>`;
  }
  g += `<line x1="${M.l}" x2="${W - M.r}" y1="${H - M.b}" y2="${H - M.b}" stroke="#3a3f48"/>`;
  series.forEach((s, i) => {
    const xy = pts.map(p => [X(p.t), Y(p[s.k])]), line = smoothPath(xy);
    g += `<path d="${line} L${xy[xy.length - 1][0]},${H - M.b} L${xy[0][0]},${H - M.b} Z" fill="url(#g${i})"/>` +
         `<path d="${line}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>`;
  });
  if (hover !== null) {
    const x = X(pts[hover].t);
    g += `<rect x="${x}" y="${M.t}" width="${W - M.r - x}" height="${ph}" fill="${BG}" opacity=".55"/>` +
         `<line x1="${x}" x2="${x}" y1="${M.t}" y2="${H - M.b}" stroke="#fff" stroke-width="1.5"/>`;
  }
  g += `<rect id="hit" x="${M.l}" y="0" width="${pw}" height="${H}" fill="transparent"/>`;
  const svg = $("svg"); svg.setAttribute("height", H); svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.innerHTML = g;

  const hit = $("hit");
  const move = e => {
    const r = svg.getBoundingClientRect(), cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    const tt = t0 + Math.min(Math.max((cx - M.l) / pw, 0), 1) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(pts[i].t - tt) < Math.abs(pts[best].t - tt)) best = i;
    if (best !== hover) { hover = best; render(); showTip(X(pts[best].t), series, Y); }
  };
  hit.onmousemove = move; hit.ontouchmove = move;
  hit.onmouseleave = hit.ontouchend = () => { hover = null; $("tip").style.display = "none"; render(); };
  setHeader();
  if (hover !== null) showTip(X(pts[hover].t), series, Y);
}
function showTip(x, series, Y) {
  const p = pts[hover], tip = $("tip");
  const row = (l, v) => `<div class="row"><span>${l}</span><b>${money(v)}</b></div>`;
  tip.innerHTML = `<div class="t">${fmtDate(p.t)}</div>` +
    (view === "returns" ? `<div class="t" style="margin:0">All-Time Returns</div><b>${money(p.r)}</b>`
                        : row("Portfolio Value", p.v) + row("Net Deposits", p.c) + row("All-Time Returns", p.r));
  tip.style.display = "block";
  const w = tip.offsetWidth, y = Y(p[series[0].k]);
  tip.style.left = (x + 14 + w > $("plot").clientWidth ? x - 14 - w : x + 14) + "px";
  tip.style.top = Math.max(0, Math.min(y + 8, H - tip.offsetHeight)) + "px";
}
function setView(v) {
  view = v; hover = null;
  $("vReturns").classList.toggle("on", v === "returns"); $("vEquity").classList.toggle("on", v === "equity");
  render();
}
$("vReturns").onclick = () => setView("returns"); $("vEquity").onclick = () => setView("equity");
RANGES.forEach(r => {
  const b = document.createElement("button"); b.textContent = r; b.dataset.r = r;
  b.onclick = () => { range = r; hover = null; document.querySelectorAll("#pills button")
    .forEach(x => x.classList.toggle("on", x.dataset.r === r)); render(); };
  if (r === range) b.classList.add("on");
  $("pills").appendChild(b);
});
window.addEventListener("resize", render);
render();
</script>
"""


# --- Alpha vs index card -------------------------------------------------------------
ALPHA_HEIGHT = 520


def alpha_card_html(
    curve: list[dict], index_history: list[tuple[str, float]], default_range: str = "YTD"
) -> str:
    """Hover-able "You vs EGX30" card: your time-weighted return, the index, and the alpha between.

    ``curve`` is ``performance.equity_curve`` output. Each day carries ``g`` (chained time-weighted
    growth, deposits aren't gains) and ``x`` (index level carried over non-trading days); the
    selected range is rebased to zero client-side, so switching ranges and hovering never rerun
    the app (same behaviour as the Return Analytics card above it).
    """
    idx = sorted(index_history or [])
    idx_dates = [d for d, _ in idx]
    points = []
    g, prev_v, prev_c = 1.0, 0.0, 0.0
    for r in curve:
        v, c = float(r["value"]), float(r["contributed"])
        if prev_v > 0:
            g *= (v - (c - prev_c)) / prev_v
        prev_v, prev_c = v, c
        if v <= 0:
            continue
        k = bisect_right(idx_dates, r["date"]) - 1
        if k < 0:
            continue
        points.append({"d": r["date"], "g": round(g, 6), "x": idx[k][1]})
    return _ALPHA_TEMPLATE.replace("__DATA__", json.dumps(points)).replace(
        "__RANGE__", json.dumps(default_range)
    )


_ALPHA_TEMPLATE = r"""
<style>
  html,body{margin:0;background:transparent;color:#e6e6e6;font-family:"Source Sans Pro",system-ui,sans-serif}
  .card{padding:4px 2px;user-select:none}
  .lbl{color:#8a8f98;font-size:.9rem;margin-bottom:2px}
  .head{display:flex;justify-content:space-between;align-items:flex-end;min-height:62px}
  .big{font-size:2rem;line-height:1.15;font-weight:500}
  .side{display:flex;font-size:.95rem}
  .side>div{padding:0 14px;text-align:right}
  .side>div+div{border-left:1px solid #2b2f36;padding-right:0}
  .side b{display:block;font-weight:500;margin-top:6px}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle}
  .up{color:#7ed957}.down{color:#ff4d6a}
  #plot{position:relative;margin-top:10px}
  svg{display:block;width:100%;overflow:visible}
  #tip{position:absolute;pointer-events:none;background:#2a2d33;border-radius:6px;padding:10px 14px;
    font-size:.85rem;display:none;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.5)}
  #tip .t{color:#9aa0a6;margin-bottom:4px}
  #tip .row{display:flex;gap:18px;justify-content:space-between}
  .pills{display:flex;justify-content:center;gap:10px;margin-top:18px}
  .pills button{background:transparent;border:1px solid transparent;color:#fff;font:inherit;font-size:.9rem;
    font-weight:600;cursor:pointer;border-radius:4px;padding:7px 12px}
  .pills button.on{background:#2a2d33}
  .empty{color:#8a8f98;padding:30px 0;text-align:center}
</style>
<div class="card">
  <div class="head">
    <div><div class="lbl" id="lbl"></div><div class="big" id="big"></div></div>
    <div class="side">
      <div><span class="dot" style="background:#7ed957"></span><span class="lbl">You</span><b id="sy"></b></div>
      <div><span class="dot" style="background:#9aa0a6"></span><span class="lbl">EGX30</span><b id="sx"></b></div>
    </div>
  </div>
  <div id="plot"><svg id="svg"></svg><div id="tip"></div></div>
  <div class="pills" id="pills"></div>
</div>
<script>
const D = __DATA__.map(p => ({...p, t: Date.parse(p.d + "T00:00:00Z")}));
const GREEN = "#7ed957", RED = "#ff4d6a", GREY = "#9aa0a6", GOLD = "#f1c40f", BG = "#0e1117";
const RANGES = ["1M", "3M", "6M", "YTD", "All"];
let range = __RANGE__, hover = null, pts = [];
const H = 320, M = {l: 46, r: 6, t: 8, b: 8};
const $ = id => document.getElementById(id);

function fmtDate(t) { return new Date(t).toLocaleDateString("en-GB", {day: "2-digit", month: "short", year: "numeric", timeZone: "UTC"}); }
function sgn(x, d) { return (x >= 0 ? "+" : "\u2212") + Math.abs(x).toFixed(d == null ? 2 : d); }
function cls(x) { return x >= 0 ? "up" : "down"; }
function sliceRange() {
  if (!D.length) return [];
  const last = D[D.length - 1], day = 86400000;
  let cut = 0;
  if (range === "1M") cut = last.t - 31 * day;
  else if (range === "3M") cut = last.t - 92 * day;
  else if (range === "6M") cut = last.t - 183 * day;
  else if (range === "YTD") cut = Date.UTC(new Date(last.t).getUTCFullYear(), 0, 1);
  let out = D.filter(p => p.t >= cut);
  if (out.length < 2 && D.length >= 2) out = D.slice(-2);
  return out;
}
function niceTicks(lo, hi, n) {
  const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag, out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}
function path(xy) { return xy.map((p, i) => (i ? "L" : "M") + p[0] + "," + p[1]).join(""); }
function setHeader() {
  if (!pts.length) return;
  const p = pts[hover === null ? pts.length - 1 : hover];
  $("lbl").textContent = "Alpha vs EGX30" + (hover === null ? " (since " + fmtDate(pts[0].t) + ")" : " on " + fmtDate(p.t));
  $("big").innerHTML = `<span class="${cls(p.a)}">${sgn(p.a)} pp</span>`;
  $("sy").innerHTML = `<span class="${cls(p.me)}">${sgn(p.me)}%</span>`;
  $("sx").innerHTML = `<span class="${cls(p.ix)}">${sgn(p.ix)}%</span>`;
}
function render() {
  pts = sliceRange();
  if (pts.length < 2) { $("plot").innerHTML = '<div class="empty">Not enough history in this range yet.</div>'; return; }
  const f = pts[0];
  pts = pts.map(p => { const me = (p.g / f.g - 1) * 100, ix = (p.x / f.x - 1) * 100; return {...p, me, ix, a: me - ix}; });
  if (!$("svg")) $("plot").innerHTML = '<svg id="svg"></svg><div id="tip"></div>';
  const W = $("plot").clientWidth, pw = W - M.l - M.r, ph = H - M.t - M.b;
  const series = [{k: "ix", color: GREY, w: 1.6}, {k: "me", color: GREEN, w: 2}, {k: "a", color: GOLD, w: 2, dash: "5 4"}];
  let lo = 0, hi = 0;
  for (const s of series) for (const p of pts) { lo = Math.min(lo, p[s.k]); hi = Math.max(hi, p[s.k]); }
  const pad = (hi - lo) * 0.12 || 1; lo -= pad; hi += pad;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const X = t => M.l + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * pw;
  const Y = v => M.t + (1 - (v - lo) / (hi - lo)) * ph;
  let g = "";
  for (const v of niceTicks(lo, hi, 5)) {
    const y = Y(v), zero = Math.abs(v) < 1e-9;
    g += `<line x1="${M.l}" x2="${W - M.r}" y1="${y}" y2="${y}" stroke="${zero ? "#5a5f6a" : "#262a31"}"/>` +
         `<text x="${M.l - 8}" y="${y + 4}" text-anchor="end" fill="#8a8f98" font-size="12">${Math.round(v * 10) / 10}%</text>`;
  }
  for (const s of series) {
    g += `<path d="${path(pts.map(p => [X(p.t), Y(p[s.k])]))}" fill="none" stroke="${s.color}" stroke-width="${s.w}" ` +
         `stroke-linejoin="round"${s.dash ? ` stroke-dasharray="${s.dash}"` : ""}/>`;
  }
  if (hover !== null) {
    const p = pts[hover], x = X(p.t);
    g += `<rect x="${x}" y="${M.t}" width="${W - M.r - x}" height="${ph}" fill="${BG}" opacity=".55"/>` +
         `<line x1="${x}" x2="${x}" y1="${M.t}" y2="${H - M.b}" stroke="#fff" stroke-width="1.5"/>`;
    for (const s of series) g += `<circle cx="${x}" cy="${Y(p[s.k])}" r="4" fill="${s.color}" stroke="${BG}" stroke-width="2"/>`;
  }
  g += `<rect id="hit" x="${M.l}" y="0" width="${pw}" height="${H}" fill="transparent"/>`;
  const svg = $("svg"); svg.setAttribute("height", H); svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.innerHTML = g;
  const hit = $("hit");
  const move = e => {
    const r = svg.getBoundingClientRect(), cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    const tt = t0 + Math.min(Math.max((cx - M.l) / pw, 0), 1) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(pts[i].t - tt) < Math.abs(pts[best].t - tt)) best = i;
    if (best !== hover) { hover = best; render(); }
  };
  hit.onmousemove = move; hit.ontouchmove = move;
  hit.onmouseleave = hit.ontouchend = () => { hover = null; render(); };
  setHeader();
  if (hover !== null) {
    const p = pts[hover], tip = $("tip"), x = X(p.t);
    const row = (l, c, v, u) => `<div class="row"><span><span class="dot" style="background:${c}"></span>${l}</span><b class="${cls(v)}">${sgn(v)}${u}</b></div>`;
    tip.innerHTML = `<div class="t">${fmtDate(p.t)}</div>` + row("You", GREEN, p.me, "%") + row("EGX30", GREY, p.ix, "%") + row("Alpha", GOLD, p.a, " pp");
    tip.style.display = "block";
    const w = tip.offsetWidth;
    tip.style.left = (x + 14 + w > W ? x - 14 - w : x + 14) + "px";
    tip.style.top = Math.max(0, Math.min(Y(p.a) + 8, H - tip.offsetHeight)) + "px";
  }
}
RANGES.forEach(r => {
  const b = document.createElement("button"); b.textContent = r; b.dataset.r = r;
  b.onclick = () => { range = r; hover = null; document.querySelectorAll("#pills button")
    .forEach(x => x.classList.toggle("on", x.dataset.r === r)); render(); };
  if (r === range) b.classList.add("on");
  $("pills").appendChild(b);
});
window.addEventListener("resize", render);
render();
</script>
"""
