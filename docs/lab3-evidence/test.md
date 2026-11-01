Lab 3 used the SageMaker MLflow App **`northstar-dev-mlflow`** (MLflow 3.10.1),
ARN `arn:aws:sagemaker:us-east-1:061624868577:mlflow-app/app-ZG6GAAGIQESD`.
The committed App description is in
[`lab3-evidence/mlflow-app.json`](lab3-evidence/mlflow-app.json), and the full
`mlflow.search_runs` export is in
[`lab3-evidence/mlflow-runs.csv`](lab3-evidence/mlflow-runs.csv).

The comparison was retrieved from the App with:

```python
import mlflow

mlflow.set_tracking_uri(
    "arn:aws:sagemaker:us-east-1:061624868577:mlflow-app/app-ZG6GAAGIQESD"
)
runs = mlflow.search_runs(experiment_names=["northstar-churn"])
print(runs[[
    "tags.mlflow.runName",
    "params.max_depth",
    "params.eta",
    "params.num_round",
    "metrics.auc_roc",
    "metrics.precision_top10",
    "metrics.recall_top10",
    "metrics.auc_vs_baseline",
]].sort_values("metrics.auc_roc", ascending=False).to_string(index=False))
```

`mlflow.search_runs` comparison output (three distinct hyperparameter
combinations):

| Run name | `max_depth` | `eta` | `num_round` | AUC-ROC | Precision@10% | Recall@10% | AUC vs. baseline |
|---|---:|---:|---:|---:|---:|---:|---:|
| `xgb-depth4-eta005` | 4 | 0.05 | 200 | 0.7835 | 0.7233 | 0.3288 | +0.0610 |
| `xgb-depth6-eta010` | 6 | 0.10 | 200 | 0.7793 | 0.7100 | 0.3227 | +0.0568 |
| `xgb-depth8-eta015-final` | 8 | 0.15 | 200 | 0.7679 | 0.6900 | 0.3136 | +0.0454 |

All three runs have status `FINISHED` in experiment `northstar-churn`. The
export also records the shared parameters (`subsample=0.8`,
`colsample_bytree=0.8`, `min_child_weight=5`) and the run IDs and artifact URIs,
so the table is reproducible rather than a manually transcribed comparison.