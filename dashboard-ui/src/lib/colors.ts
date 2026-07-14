const KIND_GROUPS: Record<string, string> = {
  fill: "fill",
  research_position_opened: "fill",
  research_position_closed: "fill",
  stop_hit: "order",
  order_rejected: "order",
  analysis_decision: "signal",
  research_vetting_run: "batch",
  research_drain_run: "batch",
  baseline_screen_run: "batch",
  daily_cycle_run: "batch",
  daily_cycle_completed: "batch",
  falsifier_tripped: "error",
  daily_halt: "error",
  kill_switch: "error",
  stop_failed: "error",
  startup_halted: "error",
  inconsistency: "error",
  guardian_check_error: "error",
  heartbeat_error: "error",
  research_escalation: "memo",
  resolution_due: "memo",
  catalyst_due: "memo",
  service_started: "muted",
  service_stopping: "muted",
};

export const kindClass = (kind: string) => "k-" + (KIND_GROUPS[kind] ?? "muted");

const SIDES = new Set(["buy", "sell", "short", "cover"]);
export const sideClass = (side: string) => {
  const s = side.toLowerCase();
  return "side-" + (SIDES.has(s) ? s : "other");
};
