.PHONY: install test-unit test-integration test-all lint package clean import-players tf-init tf-plan tf-apply

LAMBDA_FUNCTIONS := announcement_sender email_processor reminder_checker
BUILD_DIR := build

# Install all development dependencies
install:
	pip install -r requirements-dev.txt

# Run unit tests
test-unit:
	pytest tests/unit/ -v

# Run integration tests with LocalStack
test-integration:
	docker compose up -d
	@echo "Waiting for LocalStack to be ready..."
	@timeout 30 bash -c 'until curl -s http://localhost:4566/_localstack/health | grep -q "running"; do sleep 1; done' \
		|| (echo "LocalStack failed to start"; docker compose down; exit 1)
	pytest tests/integration/ -v; \
	EXIT_CODE=$$?; \
	docker compose down; \
	exit $$EXIT_CODE

# Run all tests
test-all: test-unit test-integration

# Lint placeholder
lint:
	@echo "TODO: configure linter (e.g. ruff, flake8)"

# Package each Lambda function into a zip with shared common code
package:
	rm -rf $(BUILD_DIR)
	@for fn in $(LAMBDA_FUNCTIONS); do \
		echo "Packaging $$fn..."; \
		mkdir -p $(BUILD_DIR)/$$fn; \
		cp -r src/common $(BUILD_DIR)/$$fn/common; \
		cp -r src/$$fn/* $(BUILD_DIR)/$$fn/; \
		cd $(BUILD_DIR)/$$fn && zip -r ../$$fn.zip . && cd ../..; \
		rm -rf $(BUILD_DIR)/$$fn; \
		echo "Created $(BUILD_DIR)/$$fn.zip"; \
	done

# Clean build artifacts
clean:
	rm -rf $(BUILD_DIR)/ .pytest_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Import players from CSV into DynamoDB
import-players:
	python scripts/import_players.py \
		--csv-file scripts/sample_players.csv \
		--table-name Players \
		--region eu-west-1

# Terraform commands
tf-init:
	cd terraform && terraform init

tf-plan:
	cd terraform && terraform plan

tf-apply:
	cd terraform && terraform apply
