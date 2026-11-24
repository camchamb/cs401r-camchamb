# NorthStar Churn Monitoring and Reliability Runbook

## Task 2: Drift detection plan

### Likely drift types

**Data drift is the most immediate risk.** NorthStar's purchase behavior changes
with holiday promotions, loyalty campaigns, catalog refreshes, store openings,
and channel shifts between online and physical stores. Those events can move
recency, purchase frequency, order value, category diversity, and spend even
when the relationship between those features and churn has not changed.
Economic shifts can also reduce discretionary spending across the customer
base, making a production window unlike the April 1, 2026 training cutoff.

**Concept drift is likely but slower to confirm.** A catalog or fulfillment
change may alter what low frequency means: customers can buy less often without
becoming more likely to churn, or a new subscription program can make recency
less predictive. Competitor pricing and changing customer expectations can
similarly change the relationship between the eleven inputs and the 90-day
churn label.

**Model degradation is the operational consequence.** It can result from data
or concept drift, missing feature updates, schema defects, or a stale model.
NorthStar tracks it separately because stable input distributions do not prove
that recall and fairness remain acceptable.

### Statistical tests and thresholds

The reference baseline is the 9,999-customer Lab 2 feature snapshot at the
April 1, 2026 feature cutoff. Each run compares it with at least 500 distinct
captured production requests from one variant. These tests
exactly match `DRIFT_PLAN` in `monitoring/evidently_drift.py`:

| Feature | Test | Baseline window | Comparison window | Drift threshold | Reason |
|---|---|---|---|---:|---|
| `days_since_last_purchase` | PSI | Lab 2 snapshot through 2026-04-01 | Latest ≥500 distinct captured requests | PSI > 0.20 | Business-critical recency bands make population shift more useful than a mean-only test. |
| `purchase_frequency_30d` | PSI | Lab 2 snapshot through 2026-04-01 | Latest ≥500 distinct captured requests | PSI > 0.20 | Detects movement among zero-, low-, and high-frequency customer segments. |
| `avg_order_value` | PSI | Lab 2 snapshot through 2026-04-01 | Latest ≥500 distinct captured requests | PSI > 0.20 | Robustly exposes shifts caused by promotions, inflation, or catalog mix. |
| `category_diversity_score` | KS | Lab 2 snapshot through 2026-04-01 | Latest ≥500 distinct captured requests | KS p-value < 0.05 | Tests the full numerical distribution without assuming a parametric form. |
| `total_spend_90d` | KS | Lab 2 snapshot through 2026-04-01 | Latest ≥500 distinct captured requests | KS p-value < 0.05 | Detects broad changes in recent spend caused by seasonality or economic pressure. |

PSI is a distance statistic, so higher values indicate drift. Evidently reports
KS as a p-value, so lower values indicate drift. A window below 500 records is
marked underpowered and cannot authorize retraining or promotion by itself.

### Concept-drift detection while labels mature

The authoritative signal is Recall@10% on outcomes after the full 90-day
holdout, so confirmed concept drift has a **90-day label lag**, plus up to seven
days for the weekly evaluation. In the meantime, NorthStar uses the seven-day
retention-campaign response rate among customers in the model's top decile as
the proxy. The signal is available with a **7-day lag** and is compared with
the trailing eight comparable campaigns, segmented by loyalty tier. A drop of
more than 20% relative to that baseline for two campaigns opens a P3 review;
it does not automatically retrain because promotions, offer quality, and
delivery failures can also reduce response.

The team also watches the immediate score distribution and `ChurnAlertVolume`.
A score shift with stable inputs suggests calibration or serving problems; a
response-rate drop with stable input and score distributions is stronger early
evidence of concept drift. Once labels mature, weekly Recall@10% and loyalty-
tier recall gaps supersede the proxies.

## Task 4: Service-level objectives

Budgets use a 30-day month and a planning volume of 1,000,000 predictions. The
event budgets scale linearly with actual request volume.

| SLO | Target | SLI (good events / total events) | Numeric error budget | Deployment freeze trigger |
|---|---|---|---|---|
| Availability | 99.5% | Successful predictions / total requests | 216 unavailable minutes per 30-day month, equivalent to 5,000 failed requests per 1,000,000 | Freeze when 162 minutes or 3,750 failed requests (75% of budget) are consumed, or immediately during an unresolved P1 outage. |
| Latency (p95) | <20 ms (`ModelLatency` ≤ `20000` µs) | Requests completing in <20 ms / total requests | 50,000 slow requests per 1,000,000 (5%) | Freeze when more than 25,000 slow requests consume 50% of the monthly budget, or p95 exceeds `20000` µs for three consecutive 5-minute periods. |
| Prediction quality | Recall@10% ≥0.25 on weekly sample | Weekly samples with Recall@10% ≥0.25 / total weekly samples | 0 failed weekly evaluations per month | Freeze model promotions after one confirmed weekly result below 0.25; require a reproducibility rerun before declaring the breach. |
| Fairness | Recall gap across loyalty tiers ≤10 percentage points | Weekly evaluations within the 10pp gap / total weekly evaluations | 0 breached weekly evaluations per month | Freeze promotion after one confirmed weekly gap >10pp with at least 30 positives per compared tier; page Model Risk if the breach persists on rerun. |

The 20 ms latency SLO is intentionally stricter than the 200 ms operational
alarm. The SLO governs a monthly budget; the alarm threshold identifies an
acute incident requiring a human response.

## Runbook: Data drift detected

### Detection

- Alert: `northstar-model-drift-p3`; `DriftDetected ≥ 1` after a completed,
  adequately powered Evidently run. The scenario-specific trigger is PSI
  greater than 0.20 on `days_since_last_purchase`.
- Confirm with `drift_violations.json`, `FeatureDriftValue`, captured-record
  count ≥500, `underpowered_window: false`, and recent data-pipeline status.

### Triage (< 5 minutes)

- Step 1: Open the latest drift report and verify the method, threshold,
  baseline size, production-window size, variant, and duplicate count.
- Step 2: Check recent promotions, catalog campaigns, Glue job runs, feature
  null/range checks, and S3 capture timestamps to distinguish real behavior
  change from stale or malformed data.
- Step 3: Compare `days_since_last_purchase` PSI with the other four monitored
  features and segment the shift by loyalty tier before changing production.

### Containment Options

- Option A: Keep the current model serving but suspend promotion and automated
  retention actions while Model Risk reviews the report. Choose this when the
  endpoint and features are healthy but the population genuinely shifted.
- Option B: Gracefully degrade to the approved champion plus the recency-rule
  baseline, cap outreach volume, and require Marketing Operations review.
  Choose this when drift changes decisions materially and retraining cannot be
  completed immediately.

### Escalation

- Trigger for escalating beyond on-call: three or more features violate their
  thresholds, PSI exceeds 0.50, drift persists in two independent windows, or
  campaign response drops more than 20%.
- Who is paged: Model Risk lead and MLEngineer lead; notify the Customer Data
  owner if source-data quality is implicated.
- SLA for response: P3 acknowledgement within 4 business hours; escalate to P2
  with a 15-minute acknowledgement if unsafe targeting is suspected.

### Resolution Verification

- Re-run Evidently on at least 500 new distinct requests and confirm the
  affected feature is within threshold or that an approved new baseline is
  documented.
- `DriftDetected` must return to `0`, `UnderpoweredWindow` must be `0`, and the
  processing job must be `Completed`. Validate Recall@10% and tier recall gaps
  before promoting a retrained package.

### Post-Incident Actions

- Within 24 hours: preserve the report, identify the business or data event,
  assign owners for retraining/baseline review, and record the decision in the
  model release ticket.
- Postmortem required: yes when drift persists across two windows, causes a
  rollback or campaign hold, or is traced to a data-contract violation;
  otherwise document the review in the weekly monitoring record.

## Runbook: Inference latency spike

### Detection

- Alert: `northstar-inference-latency-p2`; champion p95 `ModelLatency` greater
  than `500000` microseconds (500 ms) for two consecutive 5-minute periods.
- Confirm with p50/p95/p99, `Invocation5XXErrors`, `Invocations`, CPU and memory
  for both variants, endpoint status, and deployment timestamps. Ignore a lone
  cold-start point only when subsequent periods are healthy.

### Triage (< 5 minutes)

- Step 1: Confirm the units are microseconds, identify the affected variant,
  and check `describe-endpoint` for `Updating`, `Failed`, or capacity changes.
- Step 2: Compare latency with CPU, memory, request volume, payload size, 5XX
  errors, and recent deployment/configuration events.
- Step 3: Directly invoke each variant with `sample.csv` to separate endpoint
  saturation from a variant-specific container or model problem.

### Containment Options

- Option A: Shift traffic away from the affected variant and restore the known
  healthy champion. Choose this for a canary-specific regression.
- Option B: Gracefully degrade to queued batch scoring and pause synchronous
  retention decisions while continuing to accept requests. Choose this when
  both variants are slow or capacity cannot be restored immediately; return a
  retry/job identifier rather than timing out repeatedly.

### Escalation

- Trigger for escalating beyond on-call: p95 remains above `500000` µs for 10
  minutes after containment, 5XX errors exceed 5%, or both variants are affected.
- Who is paged: Platform Operations manager and MLEngineer lead; notify
  Marketing Operations when the campaign scoring deadline is threatened.
- SLA for response: P2 acknowledgement within 15 minutes; upgrade to P1 with a
  5-minute acknowledgement if predictions are unavailable or the deadline is
  at risk.

### Resolution Verification

- Confirm the endpoint is `InService`, direct smoke tests succeed, and no new
  5XX errors appear for 15 minutes.
- Champion p95 `ModelLatency` must remain below `20000` µs for three consecutive
  5-minute periods; the P2 alarm must return to `OK`, with CPU and memory back
  inside their normal ranges.

### Post-Incident Actions

- Within 24 hours: preserve metric graphs and logs, calculate latency-budget
  consumption, identify whether deployment, load, payload, or capacity caused
  the event, and create an owned corrective action.
- Postmortem required: yes for any P1, rollback, missed scoring deadline, more
  than 30 minutes above 500 ms, or recurrence within 30 days; otherwise attach
  the incident timeline to the release ticket.
