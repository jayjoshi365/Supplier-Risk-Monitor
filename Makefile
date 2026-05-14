.PHONY: test test-all test-integration install build deploy deploy-dev clean invoke-scheduled

## Run unit tests (excludes integration tests — no network required)
test:
	pytest tests/ -v --tb=short --ignore=tests/test_integration.py

## Run all tests including integration (requires network — hits real EDGAR API)
test-all:
	pytest tests/ -v --tb=short

## Run only integration tests
test-integration:
	pytest tests/test_integration.py -v -s

## Install development dependencies
install:
	pip install -r requirements-dev.txt

## Build SAM package
build:
	sam build --no-use-container

## First deploy (interactive — configure alert email and schedule)
deploy:
	sam deploy --guided

## Non-interactive deploy to prod (requires samconfig.toml)
deploy-fast:
	sam build --no-use-container && sam deploy

## Deploy to dev environment
deploy-dev:
	sam build --no-use-container && \
	sam deploy --stack-name srim-dev \
	           --parameter-overrides Environment=dev \
	           --no-confirm-changeset \
	           --no-fail-on-empty-changeset

## Invoke Lambda locally with scheduled event (requires: sam build + AWS credentials)
invoke-scheduled:
	sam local invoke SrimFunction --event events/scheduled.json

## List all suppliers via API Gateway (replace URL with your deployed endpoint)
list-suppliers:
	@echo "Usage: API_URL=https://xxx.execute-api.us-east-1.amazonaws.com/prod make list-suppliers"
	curl -s $(API_URL)/suppliers | python3 -m json.tool

## Add the sample Workday supplier to a local DynamoDB table
add-sample-supplier:
	aws dynamodb put-item \
		--table-name srim-suppliers-prod \
		--item '{ \
			"ticker": {"S": "WDAY"}, \
			"supplier_name": {"S": "Workday Inc."}, \
			"category": {"S": "HR Technology"}, \
			"kraljic_position": {"S": "Strategic"}, \
			"contract_value_usd": {"N": "480000"}, \
			"contract_end_date": {"S": "2027-03-31"}, \
			"risk_threshold": {"N": "60"}, \
			"alert_email": {"S": "procurement@company.com"}, \
			"added_date": {"S": "2026-05-14"}, \
			"score_history": {"L": []} \
		}'

## Tail Lambda logs in real-time
logs:
	sam logs --name SrimFunction --stack-name srim-prod --tail

## Remove all generated build artifacts
clean:
	rm -rf .aws-sam __pycache__ .pytest_cache
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
