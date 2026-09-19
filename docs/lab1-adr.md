## ADR-001: NorthStar Platform Foundation

### Status

Accepted

### Context

NorthStar is building a shared AI platform that supports three systems: **churn scoring, LLM serving, and a third AI system** that will be added as the platform develops. The platform needs to provide a consistent foundation for storing data, controlling access, and developing and operating machine-learning workloads.

NorthStar needs an identity model from day one because the three systems will not have identical access requirements. In particular, machine-learning engineers need to train and work with models without automatically receiving permission to modify raw business data. Separating these responsibilities allows the churn-scoring and future ML workflows to use shared infrastructure while limiting the impact of an incorrectly configured workload or accidental data modification.

NorthStar also needs a predictable storage structure because the platform will contain multiple stages of ML data and artifacts. Raw inputs, processed datasets, engineered features, and trained model artifacts have different purposes and should not be mixed together. A defined S3 structure makes it possible for future churn-scoring pipelines and LLM-related workflows to locate the appropriate data without creating separate buckets for every stage of development.

### Decision

We built a **single-AZ VPC foundation in `us-east-1` with one public subnet** for Lab 1. The VPC uses CIDR block `10.0.0.0/16`, with the public subnet using `10.0.100.0/24` in `us-east-1a`. No private subnets or NAT Gateway are included in this initial platform. This topology is intentionally limited to the infrastructure required for NorthStar's initial AI development environment rather than introducing networking components that are not yet required by the three-system platform.

The public subnet is configured to assign public IP addresses because the initial ML development environment needs direct network connectivity without requiring a NAT Gateway. This keeps the Lab 1 foundation small while leaving the VPC address space large enough for later expansion when NorthStar's workloads require additional networking.

NorthStar's storage layer uses one S3 bucket with four prefixes:

* `raw/` — original source data
* `processed/` — cleaned and transformed data
* `features/` — datasets prepared for model training and inference
* `artifacts/` — trained models and other ML outputs

This structure directly supports the churn-scoring workflow, where source customer data can progress from raw data through processing and feature engineering before being consumed by a model. It also provides a shared storage convention that the LLM-serving system and future third system can follow without requiring an independent storage architecture.

The IAM model separates machine-learning responsibilities from raw-data ownership. The `northstar-dev-MLEngineer` role is allowed to perform required SageMaker ML operations, including creating training jobs, but it is not granted permission to write objects into `raw/`. This reflects NorthStar's requirement that ML engineers can build and train models without having unrestricted authority to modify the source data used by the platform. As additional systems are introduced, additional roles can be created with permissions appropriate to their responsibilities rather than expanding the permissions of the existing ML role.

NorthStar uses a **SageMaker Domain** as the managed ML development environment. This gives the platform a consistent environment for developing and training the models used by the churn-scoring system while providing a foundation that can also support future ML workloads.

### Consequences

#### What this makes easy

The four-prefix S3 structure makes it straightforward to identify where data belongs during the churn-scoring lifecycle. Engineers can distinguish source data from processed data, model features, and trained artifacts without creating separate storage conventions for each pipeline.

The IAM model also makes it easy to give an ML engineer the ability to create SageMaker training jobs while preventing writes to `raw/`. This reduces the number of permissions that must be audited when developing the churn-scoring system.

The `/16` VPC provides substantial address space for future expansion while the initial `/24` public subnet supplies **256 IPv4 addresses, with 250 currently available**. This is sufficient for the Lab 1 development environment without requiring multiple subnets immediately.

#### What this makes harder

The single public subnet creates a **single-AZ dependency**. If `us-east-1a` becomes unavailable, workloads depending on this initial network cannot simply fail over to another Availability Zone.

The lack of private subnets and NAT Gateway also means the current network is not designed for workloads that require private network isolation. Moving production components into private subnets later will require additional routing, subnet, and security configuration.

The separate S3 prefixes require engineers and future pipelines to maintain correct permissions and paths. A pipeline that accidentally writes processed data to `raw/`, for example, can undermine the intended separation between source and derived data.

#### What would cause you to revisit this decision

We would revisit the network design if NorthStar moves from initial development into production workloads requiring multi-AZ availability or private network isolation. We would also revisit the IAM model if the LLM-serving system or third system requires access patterns that cannot be represented safely with separate roles.

The storage design would need revision if NorthStar's data volume, retention requirements, or security boundaries make a single bucket insufficient. Finally, the ML environment would need reassessment if NorthStar requires infrastructure or serving capabilities that SageMaker cannot provide efficiently.

### Alternative Considered

A meaningful alternative would be to build **separate AWS accounts or VPCs for each NorthStar system**: one for churn scoring, one for LLM serving, and one for the third AI system. This would provide stronger isolation between systems and allow each team to control its own infrastructure.

We rejected this approach for the initial platform because NorthStar is explicitly building a **shared AI platform**. Separating the systems immediately would duplicate networking, storage, and identity configuration and make shared ML data and artifacts harder to access consistently. The current shared VPC, S3 structure, and role model provide common infrastructure while still separating responsibilities through IAM permissions.

### AWS Service Selection

* **Networking isolation model:** Amazon VPC was selected because NorthStar needs a defined network boundary for its shared AI platform while initially requiring only a single public subnet.
* **Storage design:** Amazon S3 was selected because NorthStar's churn-scoring workflow needs durable shared storage for raw data, processed data, features, and ML artifacts.
* **Identity model:** AWS IAM was selected because NorthStar needs role-based permissions that allow ML engineers to train models while preventing them from writing to `raw/`.
* **ML development environment:** Amazon SageMaker was selected because NorthStar needs a managed environment for developing and training the ML workloads that support churn scoring and future platform systems.
