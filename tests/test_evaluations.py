import json

from evaluations.dataset import load_dataset
from evaluations.metrics import evaluate_case
from evaluations.model_assisted import ModelAssistedScore, run_model_assisted
from evaluations.run import run_evaluation


def _config():
    return {
        "dataset": None,
        "variants": [
            {
                "name": "existing",
                "include_operational": False,
                "citation_validation_enabled": False,
            },
            {
                "name": "operational",
                "include_operational": True,
                "citation_validation_enabled": True,
            },
        ],
    }


def test_fixture_metrics_are_deterministic():
    case = next(
        case
        for case in load_dataset()["cases"]
        if case["case_id"] == "synthetic_future_source"
    )
    first = evaluate_case(case, analyst_included=True)
    second = evaluate_case(case, analyst_included=True)
    assert first == second
    assert first["lookahead_violations"] == 1
    assert first["unsupported_high_materiality_claims"] == 1


def test_offline_run_generates_valid_json_csv_markdown(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline evaluation attempted network access")

    monkeypatch.setattr("requests.get", no_network)
    payload, paths = run_evaluation(_config(), output_dir=tmp_path)
    assert payload["model_assisted_results"] == []
    assert json.loads(paths["json"].read_text())["dataset_name"].startswith("synthetic-")
    assert paths["csv"].read_text().startswith("variant,")
    assert paths["markdown"].read_text().startswith("# Offline Evaluation Report")
    assert "operational:synthetic_backlog" in payload["fixture_reports"]


def test_tool_failure_fixture_discloses_missing_data():
    case = next(
        case
        for case in load_dataset()["cases"]
        if case["case_id"] == "synthetic_provider_failure"
    )
    result = evaluate_case(case, analyst_included=True)
    assert result["tool_failure_handling"] == 1.0
    assert result["graph_completion"] == 1.0


def test_model_assisted_provider_records_model_version_rubric_and_raw_scores():
    class DummyProvider:
        provider_name = "dummy"
        model_name = "evaluator-test"
        model_version = "v1"

        def evaluate(self, *, case_id, artifact, rubric):
            return ModelAssistedScore(
                case_id=case_id,
                evaluator_provider=self.provider_name,
                evaluator_model=self.model_name,
                evaluator_version=self.model_version,
                rubric=rubric,
                raw_scores=dict.fromkeys(rubric, 0.5),
            )

    scores = run_model_assisted(DummyProvider(), [{"case_id": "case", "report": "x"}])
    assert scores[0].evaluator_version == "v1"
    assert scores[0].rubric
    assert scores[0].raw_scores
