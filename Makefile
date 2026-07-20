.PHONY: integration-up integration-env integration-test integration-down reliability-test

integration-up:
	docker compose -f docker-compose.integration.yml up -d
	@echo "Run: eval "$$(./scripts/setup-integration-env.sh)""

integration-env:
	./scripts/setup-integration-env.sh

integration-test:
	./scripts/run-integration-tests.sh

reliability-test:
	./scripts/run-reliability-checks.sh

integration-down:
	docker compose -f docker-compose.integration.yml down --remove-orphans
