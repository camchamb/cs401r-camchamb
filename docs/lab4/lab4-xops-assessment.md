# Lab 4 — XOps Assessment

## Pipeline Stage Mapping

The implementation preserves the required logical order—**Source → Test →
Build → Evaluate → Register**—without forcing each logical phase into a
separate CodePipeline stage:

| Required phase | CodePipeline / CodeBuild mapping | Enforcement and rationale |
|---|---|---|
| Source | CodePipeline `Source` stage | A GitHub connection watches `main` and emits the exact source revision consumed downstream. |
| Test | CodePipeline `Build` stage, CodeBuild `PRE_BUILD` | `test_data.py` and `test_features.py` run before training. Any failure ends `PRE_BUILD`, so neither training nor registration can start. |
| Build | CodePipeline `Build` stage, CodeBuild `BUILD`, SageMaker `TrainChurnModel` | CodeBuild packages the checked-out `models/churn` source while upserting the SageMaker Pipeline. `train_sagemaker.py` then calls this revision's `churn_model.train()` in a managed training job. |
| Evaluate | CodePipeline `Build` stage, SageMaker training evaluation plus CodeBuild `BUILD` | `churn_model.evaluate_model()` fails the training step when model-quality gates fail. After training, `test_model.py` and `test_fairness.py` verify the produced artifact, metrics, champion regression, and slice results. |
| Register | SageMaker `RegisterChurnModel`, reached from CodeBuild `BUILD` | A successful training step registers the artifact as `PendingManualApproval`. A later CodeBuild gate failure changes that run's package to `Rejected`, so only a fully green run leaves an approvable candidate. |

Source is its own artifact boundary, while Test through Register share one
CodeBuild/SageMaker execution so the tests, model artifact, evaluation metrics,
and registry entry all remain tied to the same commit. The separate
CodePipeline `Approval` and `Deploy` stages are post-registration delivery
controls; they are not substitutes for any of the five required CI phases.

## Champion-Challenger

The **champion** is the currently approved production model. A newly trained
**challenger** replaces it only when both of these conditions hold on the same
frozen, out-of-time evaluation set:

1. `AUC_challenger - AUC_champion >= 0.010`; and
2. the lower bound of the paired-bootstrap 95% confidence interval for
   `AUC_challenger - AUC_champion` is greater than `0.000`.

In other words, the challenger must improve AUC by at least **one percentage
point**, and the measured improvement must be statistically positive. Equality
at `+0.010` passes; `+0.0099` does not. The challenger must also continue to
pass the existing model-quality guardrails (`precision@10% >= 0.50`,
`recall@10% >= 0.25`, and a positive AUC-lift CI over the recency-only
baseline). The pipeline's `AUC >= champion - 0.02` test is a regression
guardrail for admitting a candidate to review; it is not sufficient by itself
to replace the champion.

For example, if the champion AUC is `0.7700`, the challenger must have AUC at
least `0.7800` and a paired-bootstrap lift-CI lower bound above zero.

## Retraining Triggers

Either trigger creates the same idempotent retraining-pipeline execution. The
execution key is the UTC week plus the feature snapshot ID, so simultaneous
signals cannot start duplicate jobs.

### Scheduled trigger

An Amazon EventBridge rule invokes the retraining pipeline every Monday at
`07:00 UTC` using `cron(0 7 ? * MON *)`. This is unconditional and requires no
human decision. The pipeline automatically builds the latest label-matured
feature snapshot, trains the configured challenger search, evaluates it, and
logs every run to MLflow. Retraining does not imply promotion: the numeric
champion–challenger rule above still controls replacement.

### Performance-based trigger

A daily `07:30 UTC` monitoring job calculates production ROC AUC on the latest
rolling 30-day cohort whose churn labels have matured. When there are at least
`1,000` labeled predictions and both classes are present, it publishes the
metric to CloudWatch. A CloudWatch alarm enters `ALARM` and invokes the same
retraining pipeline when:

```text
production_auc_30d < 0.7200 for 2 consecutive daily evaluations
```

The sample-size and two-evaluation rules are machine-evaluated protections
against noisy or undefined AUC values; they do not introduce a manual choice.
After a run starts, the alarm/execution key suppresses duplicates until that
run finishes.

## New Lab 4 MLflow experiment tracking

The complete fresh export is committed as
[`lab4-evidence/mlflow-search-runs.csv`](lab4-evidence/mlflow-search-runs.csv),
with a readable console-formatted copy at
[`lab4-evidence/mlflow-search-runs.txt`](lab4-evidence/mlflow-search-runs.txt).


| Run ID | Run name | `max_depth` | `eta` | `num_round` | AUC-ROC | Precision@10% | Recall@10% | AUC vs. baseline | Lift 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `c7f1262688b44c4dbfa12db6504a354c` | `lab4-depth4-eta005` | 4 | 0.05 | 200 | 0.7869 | 0.7167 | 0.3258 | +0.0561 | [0.0377, 0.0749] |
| `17838fd2449d4bb7a2d4ec180f825e68` | `lab4-depth6-eta010` | 6 | 0.10 | 200 | 0.7794 | 0.7133 | 0.3242 | +0.0485 | [0.0300, 0.0680] |
| `e95a5636c177444b899894e58583c0e8` | `lab4-depth8-eta015` | 8 | 0.15 | 200 | 0.7694 | 0.7100 | 0.3227 | +0.0386 | [0.0186, 0.0585] |

All three new runs have status `FINISHED`. The export also records the shared
parameters (`subsample=0.8`, `colsample_bytree=0.8`, `min_child_weight=5`),
experiment tags, timestamps, and artifact URIs, so the comparison is directly
traceable to the new MLflow experiment.

## DataOps Maturity

Current level: 2

Evidence: NorthStar has moved beyond manually prepared datasets to a repeatable,
version-controlled data workflow. Lab 1 provisions the S3 data zones, IAM
roles, networking, and SageMaker foundation through Terraform under
`infrastructure/environments/dev`. Lab 2 adds the Glue transformation in
`glue-scripts/feature_engineer.py`, offline and online SageMaker Feature Store
infrastructure, and a documented schema and quality contract in
`docs/lab2/lab2-data-contract.md`. The lineage diagram at
`docs/lab2/lab2-data-lineage.png` traces data to model training. Lab 4 makes
validation executable:
`tests/test_data.py` checks schema, nulls, ranges, label realism, leakage, and
feature availability, while `buildspec.yml` runs those checks automatically
before training. The final run in `docs/lab4-build-green.log` records all 28
data tests and all 18 feature tests passing before training. These controls
make ingestion and feature generation
repeatable and testable, which fits Level 2, but they do not yet constitute a
continuously observed production data product.

Gap to Level 3: The missing capability is automated production data
observability with explicit freshness, volume, distribution, and contract SLOs.
The current tests catch a bad batch during CI, but no deployed service monitors
every arriving batch, records longitudinal quality metrics, or automatically
quarantines invalid data and alerts an owner.

Top priority investment: Add an EventBridge-scheduled data-quality job after
each Glue load that evaluates the existing contract, publishes freshness,
row-count, null-rate, and feature-distribution metrics to CloudWatch, and moves
failed partitions to a quarantine prefix while raising an alarm. This turns
the existing checks into an operational control and advances DataOps toward
Level 3.

## MLOps Maturity

Current level: 2

Evidence: Model development and validation are reproducible and substantially
automated. `models/churn/churn_model.py` contains the training and evaluation
implementation exercised by the managed training job;
`tests/test_model.py` enforces
precision@10%, recall@10%, bootstrap AUC-lift, realistic prevalence, and
champion-regression gates. The Lab 4 `pipeline.yaml` and `buildspec.yml`
implement source-triggered build, test, SageMaker training, and registration
of a pending model package, followed by a human approval gate. The fresh
MLflow experiment `northstar-churn-lab4-xops-20261031` tracks three distinct
hyperparameter combinations; its `mlflow.search_runs` export is committed at
`docs/lab4-evidence/mlflow-search-runs.csv`. `docs/lab4-model-package.json`
records candidate v8's commit, data URI, timestamp, and metrics, while
`docs/lab4-model-registry.json` shows v6 as the approved champion and v8
pending review. `docs/lab4-build-green.log` records the live v6-versus-v8 AUC
comparison, and `docs/lab4-failure-demo.txt` demonstrates that a failed gate
blocks progression. Together these form a repeatable Level 2 pipeline.

Gap to Level 3: The specific missing capability is automated, monitored model
delivery. The pipeline stops after approval: it does not deploy the approved
package to an inference endpoint, shift traffic safely, evaluate live health,
or roll back automatically. Production drift and delayed-label performance
are also not yet feeding the retraining policy.

Top priority investment: Implement the pipeline's deployment stage as a
SageMaker canary release with CloudWatch alarms for errors, latency, and model
quality, plus automatic rollback to the champion. That closes the path from an
approved registry version to a safely operated production model and is the
most direct step toward MLOps Level 3.
