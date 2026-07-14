import type { Health, Market, Section } from "../data/types";
import { isErr } from "../data/types";
import { guardAge, hhmmss } from "../lib/format";

const VERDICT_DOT: Record<string, string> = {
  RUNNING: "d-pos", STALE: "d-amber", STOPPED: "d-sev", UNKNOWN: "d-sev",
};

export default function HeaderBar(props: {
  health: Section<Health> | null;
  market: Section<Market> | null;
  lastGoodAt: number | null;
}) {
  const h = props.health && !isErr(props.health) ? props.health : null;
  const m = props.market && !isErr(props.market) ? props.market : null;
  const verdict = h?.verdict ?? "UNKNOWN";
  const gAge = h?.guardian.age_seconds ?? null;
  const guardCls = verdict === "STOPPED" ? "bad" : verdict === "STALE" ? "warn" : "";
  return (
    <div className="hdr">
      <div className="hdr-left">
        <span className="hdr-verdict">
          <span className={`dot ${VERDICT_DOT[verdict]}`} />
          {verdict}
        </span>
        <span className="hdr-sep" />
        <span className="hdr-kv"><span className="k">broker</span>
          <span className="v">{h?.broker_mode ?? "—"}</span></span>
        <span className="hdr-kv"><span className="k">guardian</span>
          <span className={`v ${guardCls}`}>{guardAge(gAge)}</span></span>
      </div>
      <div className="hdr-right">
        <span className="market-chip">
          <span className={`dot sm ${m?.is_open ? "d-pos" : ""}`}
            style={m?.is_open ? undefined : { background: "var(--tx3)" }} />
          market {m ? (m.is_open ? "OPEN" : "CLOSED") : "—"}
          <span className="sub">
            {m?.is_open
              ? m.previous_close ? `prev close ${hhmmss(m.previous_close).slice(0, 5)}` : ""
              : m?.next_open ? `opens ${hhmmss(m.next_open).slice(0, 5)}` : ""}
          </span>
        </span>
        <span className="updated">
          <span className="dot slow-pulse" />
          updated {props.lastGoodAt ? hhmmss(new Date(props.lastGoodAt).toISOString()) : "—"}
        </span>
      </div>
    </div>
  );
}
