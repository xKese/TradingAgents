"""Run deterministic offline extension evaluations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .dataset import load_dataset, select_cases
from .metrics import aggregate_metrics, evaluate_case
from .offline import render_fixture_output
from .reporting import validate_output_files, write_results


def _load_config(path: str | Path) -> dict[str, Any]:
    """Load a JSON-formatted YAML file (JSON is a strict YAML subset)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_evaluation(
    config: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    case_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Path]]:
    dataset = load_dataset(config.get("dataset"))
    cases = select_cases(dataset, case_id)
    variants: dict[str, list[dict[str, Any]]] = {}
    fixture_reports: dict[str, str] = {}

    for variant in config["variants"]:
        name = variant["name"]
        include_operational = bool(variant.get("include_operational", False))
        validation_enabled = bool(variant.get("citation_validation_enabled", False))
        variants[name] = [
            evaluate_case(case, analyst_included=include_operational) for case in cases
        ]
        if include_operational:
            for case in cases:
                _, report = render_fixture_output(
                    case,
                    citation_validation_enabled=validation_enabled,
                )
                fixture_reports[f"{name}:{case['case_id']}"] = report

    summaries = {name: aggregate_metrics(results) for name, results in variants.items()}
    payload = {
        "dataset_name": dataset["dataset_name"],
        "fixture_notice": dataset["license_note"],
        "config": config,
        "deterministic_results": variants,
        "summaries": summaries,
        "model_assisted_results": [],
        "fixture_reports": fixture_reports,
        "output_file_validity": 0.0,
    }
    selected_output = output_dir or config.get("output_dir", "evaluation-results/smoke")
    paths = write_results(payload, selected_output)
    if not validate_output_files(paths):
        raise RuntimeError("Generated evaluation output failed structural validation.")
    payload["output_file_validity"] = 1.0
    for summary in payload["summaries"].values():
        summary["output_file_validity"] = 1.0
    paths = write_results(payload, selected_output)
    if not validate_output_files(paths):
        raise RuntimeError("Final evaluation output failed structural validation.")
    return payload, paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to evaluation config")
    parser.add_argument("--output-dir", default=None, help="Override report output directory")
    parser.add_argument("--case", default=None, help="Run one fixture case ID")
    args = parser.parse_args()

    payload, paths = run_evaluation(
        _load_config(args.config),
        output_dir=args.output_dir,
        case_id=args.case,
    )
    print(
        json.dumps(
            {
                "dataset": payload["dataset_name"],
                "variants": list(payload["summaries"]),
                "cases": sum(
                    len(results)
                    for results in payload["deterministic_results"].values()
                ),
                "outputs": {name: str(path) for name, path in paths.items()},
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
