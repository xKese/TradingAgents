# Evaluation Framework

The fork-specific `evaluations` package measures research quality, evidence
integrity, and system behavior. It does not score investment profitability.

## Offline smoke command

```bash
python -m evaluations.run --config evaluations/configs/smoke.yaml
```

The smoke configuration compares:

1. existing analysts without the operational extension;
2. the Operational Signals Analyst with citation validation disabled;
3. the Operational Signals Analyst with citation validation enabled.

`smoke.yaml` uses JSON syntax because JSON is a strict subset of YAML. This
keeps the harness dependency-free while preserving the requested `.yaml`
configuration convention.

Generated files are written under `evaluation-results/` by default and are
gitignored:

- `results.json`: raw per-case metrics, configuration, fixture reports, and
  empty-by-default model-assisted results;
- `summary.csv`: one aggregate row per comparison variant;
- `report.md`: readable metric table and interpretation cautions.

Use `--output-dir` to redirect them and `--case CASE_ID` to run one scenario.

## Dataset

`evaluations/fixtures/operational_cases.json` is entirely synthetic. No full
filing, private dataset, or copyrighted long-form source is committed. Reserved
`.test` URLs make it clear that fixture sources do not represent real pages.

Cases cover:

- reported backlog;
- no disclosed backlog;
- customer concentration and capacity expansion;
- conflicting source values for one reporting period;
- a source published after the analysis date;
- wrong-company/ticker evidence and a missing URL;
- provider failure;
- duplicate evidence.

## Deterministic metrics

`evaluations/metrics.py` computes:

- structured-output validity;
- citation coverage and citation existence;
- temporal validity and look-ahead violation count;
- unsupported high-materiality claim count;
- ticker identity correctness;
- duplicate evidence rate;
- missing-data disclosure rate;
- offline pipeline completion;
- configured analyst inclusion;
- tool-failure handling;
- checkpoint thread-ID compatibility;
- generated output-file validity.

The fixture runner exercises the real Pydantic schemas and deterministic
citation validator. `checkpoint_resume_compatibility` checks stable thread IDs
for identical graph signatures and different IDs when the operational analyst
changes graph shape. It does not claim to simulate an interrupted live LLM run;
that behavior remains covered by `tests/test_checkpoint_resume.py`.

## Optional model-assisted metrics

Subjective evaluation is deliberately separate. `EvaluatorProvider` in
`evaluations/model_assisted.py` is a provider-neutral protocol. An explicitly
supplied implementation receives the saved artifact and rubric and must return
`ModelAssistedScore`, including:

- evaluator provider;
- model identifier and version;
- complete rubric;
- raw scores;
- per-dimension rationale.

The default smoke never instantiates an evaluator or calls an API. The included
rubric covers claim-to-evidence entailment, retrieval relevance, operational
completeness, internal contradiction, and downstream use of cited evidence.
Subjective scores must be described as evaluator judgments, not ground truth.

## CI scope

The CI smoke runs after the normal pytest job has installed the project. It
uses committed fixtures, makes no paid API calls, performs no network access,
and writes artifacts only to `/tmp`. Live SEC and LLM evaluation are excluded
from pull-request CI.
