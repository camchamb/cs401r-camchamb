# CS 401R — Engineering the AI Enterprise

Coursework, labs, and projects for **BYU CS 401R: Engineering the AI Enterprise**.

This course focuses on designing, building, deploying, and operating **production-scale AI systems** in enterprise environments. The coursework progresses from cloud infrastructure and data pipelines through model development, CI/CD, deployment, monitoring, reliability, and AI business value.

## Course Overview

The course uses a running case study, **NorthStar Retail**, to develop an end-to-end enterprise AI platform on AWS. Each lab builds a different layer of the system, applying software engineering, machine learning, cloud computing, and MLOps principles to realistic production scenarios.

The course culminates in a **team-based AI systems project**, where students design and build an end-to-end AI system of their choice.

## Topics

* AWS cloud architecture
* Infrastructure as Code
* Data and feature engineering
* Feature stores
* Machine learning model development
* Retrieval-Augmented Generation (RAG)
* Prompt evaluation
* MLOps, LLMOps, DataOps, and AgentOps
* CI/CD for AI systems
* Model registries
* Canary, blue/green, A/B, and shadow deployments
* Model monitoring and data drift
* Reliability engineering and incident response
* AI system evaluation
* FinOps and AI cost management
* AI governance
* Measuring and communicating business value

## Labs

The seven labs progressively build the NorthStar Retail enterprise AI platform.

| Lab       | Topic                               | Key Technologies / Concepts                                      |
| --------- | ----------------------------------- | ---------------------------------------------------------------- |
| **Lab 1** | Platform Foundation                 | AWS, SageMaker, S3, IAM, VPC, Infrastructure as Code             |
| **Lab 2** | Data & Feature Engineering          | AWS Glue, Feature Store, Data Pipelines, Data Lineage            |
| **Lab 3** | Model Development                   | RAG, Prompt Engineering, Model Evaluation                        |
| **Lab 4** | CI/CD Pipeline                      | GitHub Actions, Model Registry, Automated Testing, Stage Gates   |
| **Lab 5** | Deployment & Scaling                | SageMaker Endpoints, Canary Deployments, Auto Scaling, Rollbacks |
| **Lab 6** | Monitoring & Reliability            | CloudWatch, MLflow/W&B, Drift Detection, Incident Response       |
| **Lab 7** | Metrics, Economics & Business Value | Metric Pyramid, FinOps, Cost Attribution, Business KPIs          |

## Course Project

The final project involves working in a team to design and implement an **end-to-end AI system**.

The project emphasizes the same production principles developed throughout the labs, including:

* System architecture
* Data and model pipelines
* AI evaluation
* Deployment
* Monitoring
* Reliability
* Cost management
* Governance
* Business value

Project materials and implementation will be added to this repository as the course progresses.

## Lab 3 — Churn model training and evaluation

Lab 3 trains the XGBoost churn model in
[`models/churn/train.py`](models/churn/train.py). It deliberately reuses the
Lab 1 data bucket and `MLEngineer` role, plus the Lab 2 Feature Store; it does
not create a bucket or IAM role. Retrieve those values from the Terraform state
rather than typing resource names:

```powershell
$env:ARTIFACTS_BUCKET = terraform -chdir=infrastructure/environments/dev output -raw s3_bucket_name
$env:FEATURE_GROUP_NAME = terraform -chdir=infrastructure/environments/dev output -raw feature_group_name
$env:ML_ENGINEER_ROLE_ARN = terraform -chdir=infrastructure/environments/dev output -raw ml_engineer_role_arn
python models/churn/train.py --max-depth 8 --eta 0.15 --num-round 200 --run-name xgb-depth8-eta015
```

For a local feature CSV, add `--local-data-path path/to/features.csv`; this
bypasses the Feature Store query but keeps the same evaluation workflow. All
training controls (including split seed, early stopping, and baseline controls)
are command-line options, and environment variables can supply deployment
paths. Run `python models/churn/train.py --help` for the complete interface.

The training job queries the Lab 2 offline Feature Store, writes Athena query
results and packaged model artifacts to the Lab 1 bucket under `artifacts/`,
and registers the packaged model for manual approval. Evaluation metrics,
confusion matrix, feature importance, and loyalty-tier slices are written to
`docs/lab3-evidence/` by default. MLflow tracking is enabled by supplying
`--mlflow-arn` (or `MLFLOW_APP_ARN`); its artifact store is the same bucket's
`artifacts/mlflow/` prefix.

## Learning Objectives

By completing this course, I have develop experience with:

1. Designing production-grade AI platforms on AWS.
2. Building data and feature engineering pipelines.
3. Developing and deploying predictive and generative AI systems.
4. Applying XOps practices across the AI lifecycle.
5. Building CI/CD pipelines for machine learning systems.
6. Evaluating AI systems using quantitative and qualitative metrics.
7. Monitoring AI systems for drift, performance degradation, and reliability issues.
8. Managing AI infrastructure and operational costs.
9. Designing governance practices for enterprise AI systems.
10. Connecting technical system performance to business outcomes.

## Technologies

Technologies used throughout the course may include:

**Cloud & Infrastructure**

* AWS
* Amazon SageMaker
* Amazon S3
* AWS Glue
* IAM
* VPC
* Terraform

**AI & Machine Learning**

* Python
* RAG
* Prompt Engineering
* Machine Learning
* Model Evaluation
* Feature Engineering
* SageMaker Model Registry

**MLOps & DevOps**

* GitHub Actions
* CI/CD
* MLflow
* Weights & Biases
* Amazon CloudWatch
* Automated Testing
* Model Deployment
