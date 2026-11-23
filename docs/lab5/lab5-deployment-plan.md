# NorthStar Churn Model Operational Deployment Plan

## 1. Deployment strategy and rationale

Use a SageMaker real-time endpoint with a weighted canary. The existing model is the `champion` variant at 90% traffic and the candidate begins as `canary` at 10%. Both variants use `ml.m5.large`; the champion has target-tracking scaling from one to two instances. This is safer than replacing the endpoint configuration directly because the same production requests exercise both models and traffic can return to the champion without endpoint downtime.

NorthStar produces churn scores weekly for the Monday 6:00 a.m. ET retention campaign, so sub-second inference is not a business requirement and a batch design would normally be economical. The real-time canary is used here because the campaign integration submits individual customer records and the team needs observable routing, immediate rollback, and a controlled comparison with the incumbent. The endpoint should exist only for the scoring and monitoring window, rather than run all week.

## 2. Pre-deployment checklist

The MLEngineer owns this checklist and release approval. From the repository root, confirm identity, registry status, endpoint health, and tests:

```bash
export AWS_DEFAULT_REGION=us-east-1
EP=northstar-churn-prod
aws sts get-caller-identity
aws sagemaker list-model-packages --model-package-group-name northstar-churn-models --output table
aws sagemaker describe-endpoint --endpoint-name "$EP"
python -m pytest tests -q
```

The MLEngineer reviews precision at the top 10%, recall at the top 10%, the AUC-lift confidence interval over the recency baseline, artifact loading, champion regression, and the fairness report. Model package v10 was approved as the initial production champion because it passed these CI quality gates, improved on the recency-only baseline with statistically supported lift, and provided a tested incumbent for controlled comparison rather than an unreviewed direct deployment. Package v13 later passed the same review and was approved as the canary through Stage 3. Push the reviewed commit, then approve Stage 3 to authorize Stage 4:

```bash
git push origin main
CANDIDATE_ARN=$(aws sagemaker list-model-packages \
  --model-package-group-name northstar-churn-models \
  --query 'ModelPackageSummaryList[?ModelPackageVersion==`13`].ModelPackageArn | [0]' \
  --output text)
aws sagemaker update-model-package --model-package-arn "$CANDIDATE_ARN" \
  --model-approval-status Approved
```

## 3. Rollback criteria

The primary rollback trigger is `AWS/SageMaker` `ModelLatency` for the `canary` variant. Alarm `northstar-churn-rollback-model-latency` enters `ALARM` when p99 latency is greater than **200,000 microseconds (200 ms)** for two consecutive 60-second periods. This exactly matches `deployment/configs/rollback-alarm.json`.

The on-call MLEngineer makes the operational call. Any alarm state, failed smoke test, malformed response, endpoint failure, or material prediction anomaly requires immediate rollback. Rollback sets champion weight to 1 and canary weight to 0, then waits for `InService`.

```bash
bash deployment/configs/rollback-action.sh
```

## 4. Monitoring window

The production plan uses a **48-hour** canary before promotion. Watch endpoint status, p99 `ModelLatency`, invocation errors, variant traffic counts, instance count, scaling alarms, captured inputs/outputs, response schema, and prediction-distribution changes. A clean deployment remains `InService`, keeps the rollback alarm `OK`, has no smoke-test failures, maintains approximately 90/10 observed routing, and shows no material prediction anomaly.

The lab compresses 48 hours to **60 minutes** to control cost. It validates mechanics, routing, alarms, scaling, and rollback, but cannot cover daily cycles, drift, Monday campaign load, or rare segments. A real rollout additionally monitors the full campaign cycle, segment slices, capture delivery, saturation, and downstream errors. Create the alarm, wait for Stage 4, attach scaling, and inspect live state:

```bash
EP=northstar-churn-prod
aws cloudwatch put-metric-alarm --cli-input-json file://deployment/configs/rollback-alarm.json
aws sagemaker wait endpoint-in-service --endpoint-name "$EP"
aws application-autoscaling register-scalable-target \
  --cli-input-json file://deployment/configs/scalable-target.json
aws application-autoscaling put-scaling-policy \
  --cli-input-json file://deployment/configs/scaling-policy.json
aws sagemaker describe-endpoint --endpoint-name "$EP"
aws cloudwatch describe-alarms --alarm-names northstar-churn-rollback-model-latency
```

After a clean hour, the scheduled Lambda promotes the candidate as sole `champion`; otherwise it rolls back.

## 5. Stakeholder notifications

At deployment start, the MLEngineer sends the release version, endpoint, 90/10 weights, monitoring window, and rollback threshold to the Platform Operations lead, Model Risk reviewer, Marketing Operations lead, and Customer Data owner. Platform Operations receives the CloudWatch dashboard and owns infrastructure escalation; Model Risk receives evaluation and fairness results; Marketing Operations receives campaign-impact timing; the Data owner receives the capture destination and retention statement.

On promotion, the same roles receive the promoted package version, alarm history, observed split, smoke-test result, and effective time. On rollback, notify them immediately with the triggering metric or failed check, affected interval, restored champion, customer impact, incident owner, and next update time. The MLEngineer records all notices in the release ticket.

## 6. Post-deployment review

Hold the review within one business day. Attendees are the MLEngineer, Platform Operations lead, Model Risk reviewer, Marketing Operations lead, and Customer Data owner. Review CI approval, endpoint and scaling behavior, alarm state/history, observed routing, rollback or promotion timing, data-capture delivery, prediction distributions, and incidents. The MLEngineer produces `docs/lab5-release-review.md` containing decisions, evidence links, deviations, owners, due dates, and whether the model remains approved.

## 7. Resource cleanup

After evidence capture, delete schedules, scaling policies/targets, alarms, endpoint, endpoint configurations, models, and in-flight jobs. Keep Model Registry metadata. Run teardown and require six successful checks:

```bash
bash scripts/teardown-lab5.sh
grep -c ' OK$' docs/lab5-teardown-output.txt
```
