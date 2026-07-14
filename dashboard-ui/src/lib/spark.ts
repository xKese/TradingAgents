export function sparkPath(
  values: number[], w: number, h: number,
): { line: string; area: string } {
  if (!values.length) return { line: "", area: "" };
  const mn = Math.min(...values);
  const mx = Math.max(...values);
  const rng = mx - mn || 1;
  const pad = h * 0.15;
  const n = values.length;
  const pts = values.map((v, i) => [
    n === 1 ? w : (i / (n - 1)) * w,
    h - pad - ((v - mn) / rng) * (h - pad * 2),
  ]);
  const line = "M" + pts.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" L");
  return { line, area: `${line} L${w} ${h} L0 ${h} Z` };
}
