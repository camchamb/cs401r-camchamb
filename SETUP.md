source .venv/bin/activate

# Prerequisite (Task B3, one-time)
bash scripts/bootstrap-state.sh


# Deploy
cd infrastructure/environments/dev
terraform plan    
terraform apply

cd ../../..


## 
export AWS_DEFAULT_REGION=us-east-1


ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="northstar-dev-data-${ACCOUNT_ID}"

echo "Account: $ACCOUNT_ID"
echo "Bucket: $BUCKET"

## upload raw data

aws s3 cp northstar-raw-sample.csv \
  "s3://${BUCKET}/raw/customers/northstar-raw-sample.csv"


## Run Glue
aws glue start-crawler \
  --name northstar-dev-raw-crawler \
  --region us-east-1


until [ "$(aws glue get-crawler \
  --name northstar-dev-raw-crawler \
  --region us-east-1 \
  --query 'Crawler.State' \
  --output text)" = "READY" ]; do
  sleep 15
done
echo "Crawler is READY"

### Verify glue
export AWS_DEFAULT_REGION=us-east-1

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="northstar-dev-data-${ACCOUNT_ID}"

echo "Account: $ACCOUNT_ID"
echo "Bucket: $BUCKET"

## Run Transform Job

TRANSFORM_RUN=$(aws glue start-job-run \
  --job-name northstar-dev-transform \
  --region us-east-1 \
  --query 'JobRunId' \
  --output text)
echo "Transform run: $TRANSFORM_RUN"


while true; do
  STATUS=$(aws glue get-job-run \
    --job-name northstar-dev-transform \
    --run-id "$TRANSFORM_RUN" \
    --region us-east-1 \
    --query 'JobRun.JobRunState' \
    --output text)
  echo "Transform status: $STATUS"
  case "$STATUS" in
    SUCCEEDED) break ;;
    FAILED|TIMEOUT|ERROR|STOPPED|EXPIRED)
      echo "Transform job failed. Inspect its logs before continuing."
      exit 1
      ;;
  esac
  sleep 20
done


aws s3 ls "s3://${BUCKET}/processed/customers/" --recursive

## Run Feature Engineering Job
FEATURE_RUN=$(aws glue start-job-run \
  --job-name northstar-dev-feature-engineer \
  --region us-east-1 \
  --query 'JobRunId' \
  --output text)
echo "Feature engineering run: $FEATURE_RUN"


while true; do
  STATUS=$(aws glue get-job-run \
    --job-name northstar-dev-feature-engineer \
    --run-id "$FEATURE_RUN" \
    --region us-east-1 \
    --query 'JobRun.JobRunState' \
    --output text)
  echo "Feature engineering status: $STATUS"
  case "$STATUS" in
    SUCCEEDED) break ;;
    FAILED|TIMEOUT|ERROR|STOPPED|EXPIRED)
      echo "Feature engineering failed. Inspect its logs before continuing."
      exit 1
      ;;
  esac
  sleep 20
done


aws s3 ls "s3://${BUCKET}/features/customers/" --recursive

aws s3 ls "s3://${BUCKET}/features/offline-store/" --recursive

aws sagemaker describe-feature-group \
  --feature-group-name northstar-dev-customer-features \
  --region us-east-1 \
  --query '{Status:FeatureGroupStatus,OfflineStore:OfflineStoreConfig.S3StorageConfig.ResolvedOutputS3Uri}'


## Verify with lab 2
bash scripts/lab2/verify-lab2.sh




# Checks
aws s3 ls \
  s3://northstar-dev-data-$(aws sts get-caller-identity --query Account --output text)/features/offline-store/ \
  --recursive

aws s3 ls \
s3://northstar-dev-data-$(aws sts get-caller-identity --query Account --output text)/features/customers/ \
--recursive

# Test
./scripts/verify-lab1.sh


# Destroy
terraform destroy