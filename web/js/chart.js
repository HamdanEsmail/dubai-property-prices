// A small SVG line chart: median AED per sqm by month for the chosen community,
// with all of Dubai as a dashed reference line. No library, works at any width.

const NS = "http://www.w3.org/2000/svg";
const PAD = { top: 18, right: 78, bottom: 34, left: 56 };
let W = 720, H = 300;

function el(name, attrs = {}, text) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function monthLabel(ym) {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleString("en-US", { month: "short" });
}

const fmt = (v) => Math.round(v).toLocaleString("en-US");

// series: [{ key: "community"|"dubai", label, data: {ym: {median, n}} }]
export function drawMonthly(svg, series, tooltipEl) {
  // Size the drawing to the container so text stays readable on a phone.
  W = Math.max(300, Math.round(svg.getBoundingClientRect().width || 720));
  H = W < 480 ? 240 : 300;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren(svg.querySelector("title"));
  const months = [...new Set(series.flatMap((s) => Object.keys(s.data)))].sort();
  const values = series.flatMap((s) => months.map((m) => s.data[m]?.median).filter((v) => v !== undefined));
  if (!months.length || !values.length) {
    svg.appendChild(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle" }, "Not enough sales to draw a monthly line."));
    return;
  }
  // round the axis to "nice" steps (1, 2, 2.5, 5 x 10^k) so ticks read as whole numbers
  const rawSpan = Math.max(...values) * 1.08 - Math.min(...values) * 0.9;
  const mag = 10 ** Math.floor(Math.log10(rawSpan / 4));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => rawSpan / s <= 5);
  const lo = Math.floor((Math.min(...values) * 0.9) / step) * step;
  const hi = Math.ceil((Math.max(...values) * 1.08) / step) * step;
  const x = (i) => PAD.left + (i / Math.max(1, months.length - 1)) * (W - PAD.left - PAD.right);
  const y = (v) => PAD.top + (1 - (v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom);

  // grid and axes
  for (let v = lo; v <= hi + 1e-9; v += step) {
    svg.appendChild(el("line", { class: "grid", x1: PAD.left, x2: W - PAD.right, y1: y(v), y2: y(v) }));
    svg.appendChild(el("text", { x: PAD.left - 8, y: y(v) + 4, "text-anchor": "end" }, fmt(v)));
  }
  months.forEach((m, i) => {
    if (months.length > 8 && i % 2 === 1) return;
    svg.appendChild(el("text", { x: x(i), y: H - 12, "text-anchor": "middle" }, monthLabel(m)));
  });

  for (const s of series) {
    const pts = months.map((m, i) => (s.data[m] ? { i, m, ...s.data[m], px: x(i), py: y(s.data[m].median) } : null)).filter(Boolean);
    if (!pts.length) continue;
    const d = pts.map((p, k) => `${k ? "L" : "M"}${p.px.toFixed(1)},${p.py.toFixed(1)}`).join(" ");
    svg.appendChild(el("path", { class: `line-${s.key}`, d }));
    const last = pts[pts.length - 1];
    svg.appendChild(el("text", { class: s.key === "community" ? "endlabel" : "", x: last.px + 8, y: last.py + 4 }, fmt(last.median)));
    if (s.key === "community") {
      for (const p of pts) {
        svg.appendChild(el("circle", { class: "dot", cx: p.px, cy: p.py, r: 3.5 }));
        const hit = el("circle", { class: "hit", cx: p.px, cy: p.py, r: 14 });
        const show = () => {
          if (!tooltipEl) return;
          const box = svg.getBoundingClientRect();
          tooltipEl.textContent = `${monthLabel(p.m)}: AED ${fmt(p.median)} per sqm, ${p.n} sales`;
          tooltipEl.style.left = `${(p.px / W) * box.width}px`;
          tooltipEl.style.top = `${(p.py / H) * box.height}px`;
          tooltipEl.hidden = false;
        };
        hit.addEventListener("mouseenter", show);
        hit.addEventListener("touchstart", show, { passive: true });
        hit.addEventListener("mouseleave", () => tooltipEl && (tooltipEl.hidden = true));
        svg.appendChild(hit);
      }
    }
  }
}
