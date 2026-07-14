import type { SeriesPoint } from "../data/types";
import { sparkPath } from "../lib/spark";

export default function Sparkline(props: {
  series: SeriesPoint[]; w: number; h: number; up: boolean; className?: string;
}) {
  const { w, h, up } = props;
  // Number() here is plotting geometry, not money display.
  const values = props.series.map((p) => Number(p.equity)).filter(Number.isFinite);
  const { line, area } = sparkPath(values, w, h);
  const stroke = up ? "var(--pos)" : "var(--neg)";
  const fill = up ? "rgba(63,178,127,.10)" : "rgba(229,100,95,.10)";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h}
      preserveAspectRatio="none" className={props.className} aria-hidden>
      <path d={area} fill={fill} />
      <path d={line} fill="none" stroke={stroke} strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
