# Portfolio Review Validation Design

## Problem

Live RKLB and OUST tests proved that per-ticker agents correctly consume IBKR holdings, but the post-batch reviewer can return an empty `actions` list and can contradict deterministic account facts. In the OUST run it omitted the required trim action, claimed 9.26% exceeded a 10% threshold, and warned that weights lacked currency reconciliation even though the loader had reconciled USD exposure to AUD NAV.

## Goal

Keep the LLM-generated portfolio narrative while enforcing deterministic action coverage and removing claims that conflict with the sanitized snapshot.

## Snapshot Metadata

The IBKR loader will add `weights_reconciled_to_base_nav: true` when all position weights are derived by allocating base-currency gross exposure across same-currency positions. The reviewer prompt will state that these percentages are authoritative NAV weights and must not be described as unreconciled or unconverted.

## Review Normalization

After structured LLM output, normalize the review against the snapshot and successful ticker decisions:

1. Ensure every ticker in `decisions` has exactly one `PortfolioAction`.
2. Preserve an existing valid action supplied by the model.
3. When an action is missing, derive it from the Portfolio Manager decision:
   - `Buy` or `Overweight` maps to `Add`.
   - `Hold` maps to `Hold existing` when owned, otherwise `Avoid`.
   - `Underweight` maps to `Trim`.
   - `Sell` maps to `Exit` when owned, otherwise `Avoid`.
4. Parse explicit whole-share instructions such as `sell 2 of 10`, `trim 1 share`, `maintain the current 2-share position`, and `exit`.
5. Populate current shares and current weight from the snapshot.
6. Calculate proposed shares and share change when the decision supplies enough information.
7. Estimate proposed weight proportionally when both current and proposed shares are known.
8. Use the decision's executive summary as the derived action rationale.

## Contradiction Filtering

Normalize prose lists conservatively:

- Remove a claim that a named position exceeds the 10% soft threshold when its authoritative weight is below 10%.
- Preserve valid statements that a position is near the threshold.
- When `weights_reconciled_to_base_nav` is true, remove warnings claiming weights were calculated without conversion, cannot be reconciled due to missing FX, or are USD values divided directly by AUD NAV.
- Preserve legitimate commentary about economic AUD/USD currency exposure.
- Do not rewrite the executive assessment or investment thesis unless they contain one of the same explicit factual contradictions.

## Failure Behavior

If a decision cannot be translated into shares, emit an action with the correct direction and `None` for unknown proposed shares rather than omitting the ticker. The CSV must therefore contain one row for every successful ticker decision.

## Testing

Add regression tests for:

- OUST Underweight with `sell 2 of 10`, producing `Trim`, current 10, proposed 8, change -2, and a proportional proposed weight.
- RKLB Hold with 2 current shares, producing `Hold existing`, proposed 2, and zero change.
- A 9.26% position not being described as exceeding 10%.
- Reconciled weights removing false FX-calculation warnings while preserving general FX-risk commentary.
- Existing valid model actions remaining unchanged.
- CSV output containing the derived action row.

## Scope

This fix changes only IBKR snapshot metadata, portfolio-review normalization, and their tests. It does not change per-ticker agents, order safety, CLI behavior, or portfolio thresholds.
