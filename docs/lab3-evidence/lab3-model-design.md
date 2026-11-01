# Lab 3 Track B — Policy-grounded retention offers

## Churn-model choice

NorthStar has a small, structured problem: 9,999 customer records and 13
available tabular fields. The final matrix uses 11 predictors;
`churn_risk_score` is the recency comparison and `loyalty_tier` is reserved for
slice evaluation. Gradient-boosted trees can learn plausible threshold effects
and interactions--a long purchase gap means something different for a frequent
shopper than a new customer--without manual feature crosses. They also handle
the mix of recency, counts, dollars, and ratios with little preprocessing.

I did not choose logistic regression because its linear log-odds form would
need manually chosen transformations and interaction terms to represent those
relationships. It would be a valuable benchmark, but not the final choice if
it gives up useful ranking quality. A neural network has the opposite problem:
with only about 7,000 training rows, 11 inputs, and no unstructured data, its
extra capacity is more likely to add tuning and overfitting risk than signal.

Interpretability matters to retention, so global gain and named inputs support
a reviewable top-decile queue; I would add local SHAP explanations before
interventions. I would switch to regularized logistic regression if its
out-of-sample ranking were effectively tied and its coefficients were more
useful to reviewers. I would consider a neural model only with substantially
more histories, sequential event data, and a cross-validated improvement over
trees that survived calibration and explanation review.

## What the features bought

The held-out 3,000-customer test set makes the feature value concrete. The
recency-only XGBoost baseline reached AUC **0.7225**. The full 11-feature model
reached **0.7679**, a **+0.0454** lift; a 2,000-replicate paired bootstrap gave
a 95% interval of **[+0.0257, +0.0649]**. This is modest, not a transformation:
recency already contains most of the usable churn signal. It is nevertheless
large enough to be credible because the interval excludes zero. At the operating
point that matters to the team, the top 10% has 0.6900 precision and captures
0.3136 of churners.

Average-gain importance supports that story. `days_since_last_purchase` is the
dominant feature (about 29 average gain), followed by
`purchase_frequency_180d` (about 20) and `purchase_frequency_90d` (about 11).
Those frequency windows tell us whether an apparently lapsed customer normally
buys often. Six-month basket size, lifetime value, 90-day spend, channel ratio,
category diversity, and tenure add smaller but nonzero gain
(roughly 7–9 each); they help distinguish similarly recent customers with
different engagement and value. `purchase_frequency_30d` has essentially zero
gain, and `avg_order_value` is also near zero in this fit. They are decorative
as currently engineered, so I would remove or redesign them only after checking
their stability across future time splits; retaining them now does not justify a
claim that every feature mattered.

The complete metric and slice discussion belongs in the separate
[`lab3-evaluation-report.md`](lab3-evaluation-report.md), with the underlying
run in [`mlflow-runs.csv`](lab3-evidence/mlflow-runs.csv).

## Why retrieval-augmented generation, not fine-tuning

Track B generates retention copy from a small, changing loyalty, shipping,
return, and FAQ corpus. RAG keeps policy outside model weights: Titan embeddings
retrieve stable-ID chunks into Claude Haiku, constrained to cited entitlements,
customer category, and 45-day single-use/account-tied terms. With fewer than
60 chunks, exact FAISS cosine retrieval is transparent and auditable.

Fine-tuning is the main rejected alternative. It can teach a voice, but cannot
keep changing policy current or provide per-answer evidence; a tier update would
require new examples and retraining, while a fluent model could still invent a
discount. Retrieval can be corrected and re-indexed without changing weights.
Its primary production risk is an incomplete or wrong entitlement yielding a
confident unsupported offer. Because feature-store tier is lifetime-value-based
while policy tier is trailing-12-month spend, tier claims stay conditional until
the system-of-record tier is supplied at send time.

## With 10× the time or data

I would replace the random split with a rolling, time-based evaluation and run
an offer-level experiment: randomly hold out eligible high-risk customers,
measure incremental repurchase and margin rather than only churn-label AUC, and
learn an uplift policy for which offer to send. On the RAG side, I would add a
structured entitlement service as the source of truth and a post-generation
claim validator that maps every benefit, dollar amount, and expiry to retrieved
policy or an approved campaign record. That changes the architecture from
“retrieve and instruct” to “retrieve, validate, then send,” which directly
addresses the costly failure mode rather than merely collecting more tests.
