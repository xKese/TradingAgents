"""Fixture loading and stable reference resolution."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any


def load_dataset(path: str | Path | None = None) -> dict[str, Any]:
    """Load the synthetic dataset from a path or packaged fixture resource."""
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    fixture = resources.files("evaluations.fixtures").joinpath("operational_cases.json")
    return json.loads(fixture.read_text(encoding="utf-8"))


def select_cases(dataset: dict[str, Any], case_id: str | None = None) -> list[dict[str, Any]]:
    cases = list(dataset.get("cases", []))
    if case_id is None:
        return cases
    selected = [case for case in cases if case.get("case_id") == case_id]
    if not selected:
        raise ValueError(f"Unknown evaluation case: {case_id}")
    return selected
