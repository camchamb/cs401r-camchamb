# Lab 2 Data Contract: Processed Customer Transactions

## Producer

Glue ETL job `northstar-dev-transform` produces the processed customer
transaction dataset.

## Consumers

- Glue job `northstar-dev-feature-engineer`
- Future Lab 3 model training

## Location and grain

The dataset is written as Parquet to `s3://<northstar-dev-data-bucket>/processed/customers/`.

**Grain:** one row per transaction. A customer can appear on many rows.

## Schema

| Column | Type | Nullable | Description |
| --- | --- | --- | --- |
| `transaction_id` | string | No | Unique source-system identifier for a purchase transaction. |
| `customer_id` | string | No | Customer identifier used to group purchase history and build features. |
| `purchase_date` | date | No | Normalized purchase date, parsed from ISO 8601 or `MM/dd/yyyy` source values. |
| `order_value` | double | No | Monetary value of the transaction; missing values are median-imputed. |
| `num_items` | integer | No | Number of items in the transaction; missing values are median-imputed and rounded. |
| `payment_method` | string | No | Payment method; missing values are represented as `unknown`. |
| `channel` | string | No | Sales channel, such as `online` or store; missing values are represented as `unknown`. |
| `store_id` | string | No | Store identifier for the transaction; missing values are represented as `unknown`. |
| `product_category` | string | No | Product category; missing values are represented as `unknown`. |

## Quality guarantees

The producer enforces the following measurable assertions before publishing:

1. `customer_id` is never null: records without a customer identifier are dropped.
2. `transaction_id` values are unique: duplicate transactions are deterministically reduced to one row per ID.
3. `purchase_date` is a valid date after parsing ISO 8601 (`yyyy-MM-dd`) or `MM/dd/yyyy`; records with an unparseable date fail the job assertion.
4. `order_value` and `num_items` are non-null after median imputation; `num_items` is stored as an integer.
5. `payment_method`, `channel`, `store_id`, and `product_category` are non-null after imputation with `unknown`.

## SLA

Processed data is available within **2 hours** of landing in `raw/customers/`.

## Versioning and change management

- Schema changes are published to a new S3 prefix.
- Breaking changes require notifying consumers at least **5 business days** in advance.
