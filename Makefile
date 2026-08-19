# LogLens — common operations.
#
# `make demo` is the one-command path from nothing to a populated dashboard.

SHELL := /bin/bash
COMPOSE := docker compose
PY := python

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Lifecycle ────────────────────────────────────────────────────────────────
.PHONY: build
build: ## Build every image
	$(COMPOSE) build

.PHONY: up
up: ## Start the whole platform in the background
	$(COMPOSE) up -d
	@echo ""
	@echo "  dashboard  http://localhost:3000"
	@echo "  API docs   http://localhost:8000/docs"
	@echo "  Grafana    http://localhost:3001  (admin / loglens)"
	@echo "  Spark UI   http://localhost:9090"

.PHONY: down
down: ## Stop everything (keeps volumes)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop everything and delete all data volumes
	$(COMPOSE) down -v

.PHONY: restart
restart: down up ## Restart the platform

.PHONY: ps
ps: ## Container status
	$(COMPOSE) ps

# ── Data ─────────────────────────────────────────────────────────────────────
.PHONY: seed
seed: ## Backfill history, then train the models on it
	$(COMPOSE) --profile seed run --rm backfill
	$(COMPOSE) --profile seed run --rm train

.PHONY: backfill
backfill: ## Generate historical metrics only (BACKFILL_HOURS=6)
	$(COMPOSE) --profile seed run --rm backfill

.PHONY: rescore
rescore: ## Re-run detection over stored history (after changing thresholds or detectors)
	$(COMPOSE) --profile seed run --rm backfill python -m generator.backfill --detect-only --hours 12

.PHONY: tune
tune: rescore evaluate ## Re-score history and report the resulting detection quality

.PHONY: train
train: ## Train the anomaly models on stored history
	$(COMPOSE) --profile ml run --rm train

.PHONY: evaluate
evaluate: ## Score detection quality against injected ground truth
	$(COMPOSE) --profile ml run --rm evaluate

.PHONY: demo
demo: build up ## Full cold start: build, launch, backfill, train
	@echo "waiting for the API to become healthy…"
	@until curl -sf http://localhost:8000/api/health >/dev/null; do sleep 3; done
	$(MAKE) seed
	@echo ""
	@echo "LogLens is ready → http://localhost:3000"

# ── Observation ──────────────────────────────────────────────────────────────
.PHONY: logs
logs: ## Tail all logs
	$(COMPOSE) logs -f --tail=80

.PHONY: logs-streaming
logs-streaming: ## Tail the Spark application
	$(COMPOSE) logs -f --tail=120 streaming

.PHONY: logs-generator
logs-generator: ## Tail the log generator
	$(COMPOSE) logs -f --tail=80 generator

.PHONY: health
health: ## Print platform health as JSON
	@curl -s http://localhost:8000/api/system/health | $(PY) -m json.tool

# ── Demo actions ─────────────────────────────────────────────────────────────
.PHONY: attack
attack: ## Inject a DDoS burst into the live stream
	@curl -sX POST http://localhost:8000/api/system/scenarios \
		-H 'Content-Type: application/json' \
		-d '{"type":"ddos_burst","intensity":1.6}' | $(PY) -m json.tool

.PHONY: outage
outage: ## Inject a service outage
	@curl -sX POST http://localhost:8000/api/system/scenarios \
		-H 'Content-Type: application/json' \
		-d '{"type":"service_outage","intensity":1.4}' | $(PY) -m json.tool

.PHONY: slow
slow: ## Inject a latency degradation
	@curl -sX POST http://localhost:8000/api/system/scenarios \
		-H 'Content-Type: application/json' \
		-d '{"type":"latency_degradation","intensity":1.5}' | $(PY) -m json.tool

# ── Development ──────────────────────────────────────────────────────────────
.PHONY: test
test: ## Run the Python test suite
	$(PY) -m pytest

.PHONY: dev-api
dev-api: ## Run the API against local infrastructure
	cd services/api && KAFKA_BOOTSTRAP_SERVERS=localhost:29092 \
		MONGO_URI='mongodb://localhost:27017/?replicaSet=rs0&directConnection=true' \
		uvicorn app.main:app --reload --port 8000

.PHONY: dev-frontend
dev-frontend: ## Run the dashboard with hot reload
	cd frontend && npm run dev

.PHONY: install-dev
install-dev: ## Install Python packages for local development
	$(PY) -m pip install -e services/common
	$(PY) -m pip install -r services/api/requirements.txt -r services/generator/requirements.txt \
		-r services/worker/requirements.txt -r services/ml/requirements.txt pytest
