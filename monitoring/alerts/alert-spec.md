# NorthStar Alert Architecture

This specification covers the churn endpoint, drift process, Glue pipeline,
and retention-business proxy. All `ModelLatency` values are CloudWatch
microseconds: 20 ms = `20000`, 200 ms = `200000`, and 500 ms = `500000`.

## Severity and escalation

| Tier | Acknowledgement | Escalation action |
|---|---:|---|
| P1 | 5 minutes, 24×7 | Wake the Platform/MLEngineer on-call and page the Platform Operations manager. Open an incident bridge and notify Marketing Operations. |
| P2 | 15 minutes, 24×7 | Page the Platform/MLEngineer on-call. Escalate to the manager after 15 minutes without acknowledgement or 30 minutes without containment. |
| P3 | 4 business hours | Post to `#northstar-ml-ops` and create an owned incident ticket due within one business day. |
| P4 | 1 business day | Create a backlog ticket assigned to the named service owner; no page. |

## Alert catalogue

| Alert name | Tier | Signal and numeric trigger | Evaluation | Owner and response |
|---|---|---|---|---|
| `northstar-endpoint-unavailable-p1` | P1 | `Invocation5XXErrors / Invocations > 0.05`, or endpoint status `Failed` | Two consecutive 1-minute periods; status failure is immediate | Platform on-call restores the champion or invokes the batch fallback; manager joins the incident bridge. |
| `northstar-campaign-deadline-p1` | P1 | Successful scored records are <90% of the scheduled campaign population 30 minutes before the Monday 06:00 ET deadline | One campaign checkpoint | MLEngineer on-call activates batch scoring; Marketing Operations decides whether to delay outreach. |
| `northstar-inference-latency-p2` | P2 | Champion p95 `ModelLatency > 500000` µs (500 ms) | Two consecutive 5-minute periods | Platform on-call follows the latency runbook and shifts traffic or enables queued batch scoring. |
| `northstar-availability-budget-p2` | P2 | Availability below 99.5%, or >3,750 failures per 1,000,000 requests (75% monthly budget consumed) | Rolling 30-day window, checked hourly | MLEngineer on-call freezes deployment and opens an availability incident. |
| `northstar-fairness-gap-p2` | P2 | Absolute Recall@10% gap between any adequately sampled loyalty tiers >10 percentage points | One weekly evaluation confirmed by rerun; ≥30 positives per tier | Model Risk and MLEngineer freeze promotion and review segment performance. |
| `northstar-offer-generation-errors-p2` | P2 | `OfferGenerationErrors / OfferGenerationRequests > 0.05` | Two consecutive 5-minute periods with at least 20 requests | Agent-platform on-call disables generated copy and gracefully falls back to the approved static offer catalogue; Marketing Operations is notified. |
| `northstar-model-drift-p3` | P3 | `DriftDetected ≥ 1` with ≥500 captured records and `UnderpoweredWindow = 0`; PSI >0.20 or KS p-value <0.05 per plan | Each completed Evidently run | MLEngineer opens a drift investigation; Model Risk reviews persistent or multi-feature drift. |
| `northstar-glue-job-failed-p3` | P3 | EventBridge `Glue Job State Change` for `northstar-dev-transform` or `northstar-dev-feature-engineer` with state `FAILED`, `ERROR`, or `TIMEOUT` | Immediate state-change event | DataEngineer checks CloudWatch logs and data-contract gates, then reruns only after correcting the cause. |
| `northstar-churn-volume-drop-p4` | P4 | `ChurnAlertVolume` is >30% below the trailing seven comparable daily runs | One daily datapoint, confirmed on the next publication | Marketing Operations and MLEngineer inspect traffic volume, score distribution, and campaign schedule. |
| `northstar-cpu-high-p4` | P4 | Champion `CPUUtilization > 80` (40% of a 2-vCPU endpoint because the metric is a per-core sum) | Three consecutive 5-minute periods | Platform owner reviews load and capacity; the multi-period rule suppresses the invalid first datapoint after deployment. |

Glue failure uses EventBridge state changes rather than
`glue.driver.aggregate.numFailedTasks`: driver-level failures can report zero
failed Spark tasks even though the job failed.

## Suppression and correlation rules

1. **Scheduled retraining suppression:** during an approved retraining or
   baseline-rebuild window, suppress `northstar-model-drift-p3` and
   `northstar-churn-volume-drop-p4` from the window start until two hours after
   the new deployment reaches `InService`. Record the change ticket and expected
   end time. Never suppress P1/P2 alerts.
2. **Endpoint root-cause correlation:** while
   `northstar-endpoint-unavailable-p1` is active, suppress duplicate P2 latency
   and P4 volume notifications but continue recording their alarm state. Send
   one correlated update on the P1 incident instead of paging repeatedly.
3. **Underpowered drift guard:** do not notify on drift when fewer than 500
   distinct records were analyzed or `UnderpoweredWindow = 1`. Create a P4 data-
   collection ticket instead; this prevents false drift alerts from small or
   duplicated smoke-test windows.
