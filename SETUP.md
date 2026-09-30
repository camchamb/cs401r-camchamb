source .venv/bin/activate

# Prerequisite (Task B3, one-time)
bash scripts/bootstrap-state.sh


# Deploy
cd infrastructure/environments/dev
terraform init
terraform plan
terraform apply 2>&1 | tee ../../../docs/lab1b-apply-output.txt


# Test
./scripts/verify-lab1.sh


# Destroy
terraform destroy