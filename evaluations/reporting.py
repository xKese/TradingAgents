"""JSON, CSV, and Markdown evaluation report writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_results(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "results.json"
    csv_path = output / "summary.csv"
    markdown_path = output / "report.md"

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summaries = payload["summaries"]
    fieldnames = ["variant", *sorted(next(iter(summaries.values())).keys())]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for variant, metrics in summaries.items():
            writer.writerow({"variant": variant, **metrics})

    lines = [
        "# Offline Evaluation Report",
        "",
        f"Dataset: `{payload['dataset_name']}`",
        "",
        "All fixtures are synthetic. Metrics assess research quality and system behavior, not returns.",
        "",
        "## Variant summary",
        "",
    ]
    metric_names = fieldnames[1:]
    lines.append("| Variant | " + " | ".join(metric_names) + " |")
    lines.append("|---|" + "---:|" * len(metric_names))
    for variant, metrics in summaries.items():
        values = [f"{metrics[name]:.4f}" for name in metric_names]
        lines.append(f"| {variant} | " + " | ".join(values) + " |")
    lines.extend(["", "## Scope and interpretation", ""])
    lines.extend(
        [
            "- Deterministic metrics are calculated from committed fixture records.",
            "- Model-assisted metrics are absent unless an evaluator provider is explicitly supplied.",
            "- A lower duplicate-evidence rate is generally preferable; conflict and look-ahead cases are intentionally adversarial.",
            "- These results do not measure or imply investment profitability.",
        ]
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "markdown": markdown_path}


def validate_output_files(paths: dict[str, Path]) -> bool:
    """Re-open generated artifacts to ensure they are structurally valid."""
    json.loads(paths["json"].read_text(encoding="utf-8"))
    with paths["csv"].open(newline="", encoding="utf-8") as handle:
        if not list(csv.DictReader(handle)):
            return False
    return paths["markdown"].read_text(encoding="utf-8").startswith(
        "# Offline Evaluation Report"
    )
