#!/usr/bin/env bash
set -euo pipefail

ENDPOINT_NAME="${ENDPOINT_NAME:-northstar-churn-prod}"

aws sagemaker update-endpoint-weights-and-capacities \
  --endpoint-name "$ENDPOINT_NAME" \
  --desired-weights-and-capacities \
    VariantName=champion,DesiredWeight=1 \
    VariantName=canary,DesiredWeight=0

aws sagemaker wait endpoint-in-service --endpoint-name "$ENDPOINT_NAME"

echo "Rollback complete: champion weight 1, canary weight 0."
