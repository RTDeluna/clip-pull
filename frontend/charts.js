// Minimal, dependency-free inline-SVG chart helpers matching the app's
// hand-drawn icon conventions (currentColor / CSS custom properties for
// fills, no external styling). No charting library exists anywhere else in
// this vanilla-JS, no-bundler frontend, and Insights only needs a simple
// trend line -- not enough surface area to justify a dependency.

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (value !== "" && value != null) node.setAttribute(key, value);
  });
  return node;
}

// Renders a filled-area trend line from an array of non-negative numbers
// (already whatever unit the caller wants: cost, token volume, or a
// normalized 0..1 shape for the blurred free-tier preview). Returns a
// detached <svg> element -- callers own inserting/replacing it in the DOM.
// `blurred` only dims the fill/line slightly; the actual CSS blur() for the
// locked state is applied to the whole .insights-pro-panels container, not
// here, so this stays a plain rendering concern.
export function lineChart(values, { width = 600, height = 160, blurred = false } = {}) {
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    preserveAspectRatio: "none",
    "aria-hidden": "true",
  });
  if (!values || values.length === 0) {
    return svg;
  }

  const max = Math.max(...values, 0.0001);
  const stepX = values.length > 1 ? width / (values.length - 1) : width;
  const points = values.map((value, i) => {
    const x = values.length > 1 ? i * stepX : width / 2;
    const y = height - (value / max) * (height - 8) - 4;
    return [x, y];
  });

  const linePath = points
    .map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");
  const lastX = points[points.length - 1][0].toFixed(1);
  const firstX = points[0][0].toFixed(1);
  const areaPath = `${linePath} L ${lastX} ${height} L ${firstX} ${height} Z`;

  svg.appendChild(
    svgEl("path", {
      class: "chart-area",
      d: areaPath,
      fill: "var(--accent)",
      "fill-opacity": blurred ? "0.08" : "0.16",
      stroke: "none",
    })
  );
  svg.appendChild(
    svgEl("path", {
      class: "chart-line",
      d: linePath,
      fill: "none",
      stroke: "var(--accent)",
      "stroke-width": "2",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    })
  );
  // A small "live" dot at the most recent point, pulsing via CSS
  // (.chart-live-dot in styles.css) -- a quiet, always-on signal that this
  // chart reflects the latest data, not a static historical snapshot.
  if (!blurred) {
    const [lastPointX, lastPointY] = points[points.length - 1];
    svg.appendChild(
      svgEl("circle", { class: "chart-live-dot__ping", cx: lastPointX, cy: lastPointY, r: 4 })
    );
    svg.appendChild(
      svgEl("circle", { class: "chart-live-dot", cx: lastPointX, cy: lastPointY, r: 3 })
    );
  }
  return svg;
}

// Animates a chart's line/area drawing in from left to right, using the
// classic stroke-dasharray/dashoffset technique. Must be called AFTER `svg`
// is attached to the live DOM -- getTotalLength() needs real layout, so this
// can't happen inside lineChart() itself, which only builds a detached
// element. No-ops (renders instantly at full length/opacity) under
// prefers-reduced-motion.
export function animateLineChart(svg) {
  const line = svg.querySelector(".chart-line");
  if (!line) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const length = line.getTotalLength();
  line.style.strokeDasharray = `${length}`;
  line.style.strokeDashoffset = `${length}`;
  const area = svg.querySelector(".chart-area");
  if (area) area.style.opacity = "0";

  // Force a reflow between setting the starting values above and the
  // transitioned end values below -- otherwise the browser coalesces both
  // into one paint and nothing visibly animates.
  void line.getBoundingClientRect();

  line.style.transition = "stroke-dashoffset 1s ease-out";
  line.style.strokeDashoffset = "0";
  if (area) {
    area.style.transition = "opacity 0.8s ease-out 0.3s";
    area.style.opacity = "1";
  }
}

// A small horizontal bar for a compact size comparison next to a list row
// (e.g. one provider's share of total cost). `ratio` is already 0..1,
// clamped defensively here since callers may hand in raw divisions.
export function barChart(ratio, { width = 80, height = 6 } = {}) {
  const clamped = Math.min(1, Math.max(0, ratio || 0));
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });
  svg.appendChild(svgEl("rect", { x: 0, y: 0, width, height, rx: height / 2, fill: "var(--glass-border)" }));
  svg.appendChild(
    svgEl("rect", { x: 0, y: 0, width: Math.max(2, width * clamped), height, rx: height / 2, fill: "var(--accent)" })
  );
  return svg;
}
