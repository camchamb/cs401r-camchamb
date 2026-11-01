set -euo pipefail

# ==========================================
# NorthStar Retail - Data Pipeline
# ==========================================

export AWS_DEFAULT_REGION=us-east-1
REGION="us-east-1"

# Get AWS account and bucket
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="northstar-dev-data-${ACCOUNT_ID}"

echo "=========================================="
echo "NorthStar Data Pipeline"
echo "=========================================="
echo "Account: $ACCOUNT_ID"
echo "Bucket: $BUCKET"
echo "Region: $REGION"

# ==========================================
# 1. Upload Raw Data
# ==========================================

echo ""
echo "Step 1: Uploading raw customer data..."

aws s3 cp northstar-raw-sample.csv \
  "s3://${BUCKET}/raw/customers/northstar-raw-sample.csv"

echo "Raw data uploaded successfully."

# ==========================================
# 2. Run Glue Crawler
# ==========================================

echo ""
echo "Step 2: Checking Glue crawler..."

CRAWLER_STATE=$(aws glue get-crawler \
  --name northstar-dev-raw-crawler \
  --region "$REGION" \
  --query 'Crawler.State' \
  --output text)

if [ "$CRAWLER_STATE" = "READY" ]; then
  echo "Crawler is already READY."
else
  if [ "$CRAWLER_STATE" = "STOPPING" ]; then
    echo "Crawler is stopping. Waiting for it to become READY..."
  elif [ "$CRAWLER_STATE" = "RUNNING" ]; then
    echo "Crawler is already running. Waiting for completion..."
  else
    echo "Starting crawler..."
    aws glue start-crawler \
      --name northstar-dev-raw-crawler \
      --region "$REGION"
  fi

  until [ "$(aws glue get-crawler \
    --name northstar-dev-raw-crawler \
    --region "$REGION" \
    --query 'Crawler.State' \
    --output text)" = "READY" ]; do
    sleep 15
  done
fi

echo "Crawler is READY."

# ==========================================
# 3. Verify Glue Table
# ==========================================

echo ""
echo "Step 3: Verifying Glue table..."

aws glue get-table \
  --database-name northstar_dev \
  --name customers \
  --region "$REGION"

echo "Glue table verified."

# ==========================================
# Helper: Wait for Glue Job
# ==========================================

wait_for_glue_job() {
  local JOB_NAME="$1"
  local RUN_ID="$2"
  local DESCRIPTION="$3"

  echo ""
  echo "Waiting for $DESCRIPTION..."

  while true; do
    STATUS=$(aws glue get-job-run \
      --job-name "$JOB_NAME" \
      --run-id "$RUN_ID" \
      --region "$REGION" \
      --query 'JobRun.JobRunState' \
      --output text)

    echo "$DESCRIPTION status: $STATUS"

    case "$STATUS" in
      SUCCEEDED)
        echo "$DESCRIPTION completed successfully."
        break
        ;;
      FAILED|TIMEOUT|ERROR|STOPPED|EXPIRED)
        echo "$DESCRIPTION failed. Inspect its logs before continuing."
        exit 1
        ;;
    esac

    sleep 20
  done
}

# ==========================================
# 4. Run Transform Job
# ==========================================

echo ""
echo "Step 4: Starting Glue transform job..."

TRANSFORM_RUN=$(aws glue start-job-run \
  --job-name northstar-dev-transform \
  --region "$REGION" \
  --query 'JobRunId' \
  --output text)

echo "Transform run: $TRANSFORM_RUN"

wait_for_glue_job \
  "northstar-dev-transform" \
  "$TRANSFORM_RUN" \
  "Transform job"

echo ""
echo "Processed customer data:"
aws s3 ls "s3://${BUCKET}/processed/customers/" --recursive

# ==========================================
# 5. Run Feature Engineering Job
# ==========================================

echo ""
echo "Step 5: Starting feature engineering job..."

FEATURE_RUN=$(aws glue start-job-run \
  --job-name northstar-dev-feature-engineer \
  --region "$REGION" \
  --query 'JobRunId' \
  --output text)

echo "Feature engineering run: $FEATURE_RUN"

wait_for_glue_job \
  "northstar-dev-feature-engineer" \
  "$FEATURE_RUN" \
  "Feature engineering job"

# ==========================================
# 6. Verify Feature Data in S3
# ==========================================

echo ""
echo "Step 6: Checking feature data..."

echo ""
echo "Customer features:"
aws s3 ls "s3://${BUCKET}/features/customers/" --recursive

echo ""
echo "Offline Feature Store:"
aws s3 ls "s3://${BUCKET}/features/offline-store/" --recursive

# ==========================================
# 7. Verify SageMaker Feature Group
# ==========================================

echo ""
echo "Step 7: Checking SageMaker Feature Group..."

aws sagemaker describe-feature-group \
  --feature-group-name northstar-dev-customer-features \
  --region "$REGION"

echo ""
echo "Feature Group details:"
aws sagemaker describe-feature-group \
  --feature-group-name northstar-dev-customer-features \
  --region "$REGION" \
  --query '{Status:FeatureGroupStatus,OfflineStore:OfflineStoreConfig.S3StorageConfig.ResolvedOutputS3Uri}'

echo ""
echo "NOTE: Offline Feature Store records may take approximately"
echo "15 minutes to appear after feature engineering completes."

# ==========================================
# COMPLETE
# ==========================================

echo ""
echo "=========================================="
echo "NorthStar Data Pipeline Completed!"
echo "=========================================="
