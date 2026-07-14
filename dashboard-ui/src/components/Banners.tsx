import type { Health } from "../data/types";
import { deriveAlert } from "../lib/alerts";
import { hhmmss } from "../lib/format";

export function DisconnectedBanner({ lastGoodAt }: { lastGoodAt: number | null }) {
  return (
    <div className="disc-banner">
      <span className="dot sm pulse" style={{ background: "var(--sev)" }} />
      <span>dashboard disconnected</span>
      <span className="when">
        — last update {lastGoodAt ? hhmmss(new Date(lastGoodAt).toISOString()) : "never"}
      </span>
    </div>
  );
}

export function AlertBanner({ health }: { health: Health | null }) {
  const alert = deriveAlert(health);
  if (!alert) return null;
  return (
    <div className={`alert-banner ${alert.tag === "ALERT" ? "alert" : "notice"}`}>
      <span className="alert-tag">{alert.tag}</span>
      <div className="alert-conds">
        {alert.conditions.map((c) => (
          <div key={c} className="alert-cond">{c}</div>
        ))}
      </div>
    </div>
  );
}
