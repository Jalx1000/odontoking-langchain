.DEFAULT_GOAL := help

DOCKER_COMPOSE ?= docker-compose
ENV            ?= development
VALID_ENVS     := development staging production test

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
define check_env
	@if ! echo "$(VALID_ENVS)" | grep -qw "$(ENV)"; then \
		echo "Invalid ENV=$(ENV). Must be one of: $(VALID_ENVS)"; exit 1; \
	fi
endef

define load_env_file
	$(call check_env)
	@ENV_FILE=.env.$(ENV); \
	if [ ! -f $$ENV_FILE ]; then \
		echo "Environment file $$ENV_FILE not found. Please create it."; exit 1; \
	fi
endef

# Shorthand: source env vars then run a command
run_with_env = bash -c "source scripts/set_env.sh $(ENV) && $(1)"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
install:
	pip install uv
	uv sync
	uv run pre-commit install

# ---------------------------------------------------------------------------
# Infrastructure (DB + Valkey + RabbitMQ sin la app)
# ---------------------------------------------------------------------------
infra-up:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) up -d db-dev valkey rabbitmq

infra-down:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) stop db-dev valkey rabbitmq

infra-logs:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) logs -f db-dev valkey rabbitmq

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
dev: infra-up
	@$(call run_with_env,uv run uvicorn app.main:app --reload --port 8000 & uv run langgraph dev --port 8123; wait)

dev-logs:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) logs -f db-dev valkey rabbitmq

staging:
	@$(call run_with_env,$(MAKE) _serve ENV=staging)

prod:
	@$(call run_with_env,$(MAKE) _serve ENV=production)

_serve:
	@$(call run_with_env,./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop uvloop)

# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------
migrate:
	@$(call run_with_env,uv run alembic upgrade head)

migration:
	@if [ -z "$(MSG)" ]; then \
		echo "Usage: make migration MSG=\"describe your change\""; exit 1; \
	fi
	@$(call run_with_env,uv run alembic revision --autogenerate -m '$(MSG)')

migrate-downgrade:
	@$(call run_with_env,uv run alembic downgrade -1)

migrate-history:
	@$(call run_with_env,uv run alembic history --verbose)

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
eval:
	@$(call run_with_env,python -m evals.main --interactive)

eval-quick:
	@$(call run_with_env,python -m evals.main --quick)

eval-no-report:
	@$(call run_with_env,python -m evals.main --no-report)

# Scenario evals: the agent plays each conversation and an LLM judge grades it
# against its success_criteria (runner -> judge -> reporter). Pass SCENARIOS=id1,id2
# to run a subset, e.g. `make eval-scenarios SCENARIOS=regresion_dias_reales_sin_fabricar`.
eval-scenarios:
	@$(call run_with_env,python -m evals.run_eval $(if $(SCENARIOS),--scenarios $(SCENARIOS),))

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run pyright

check: lint typecheck
	@echo "All checks passed"

pre-commit:
	uv run pre-commit run --all-files

pre-commit-update:
	uv run pre-commit autoupdate

# ---------------------------------------------------------------------------
# Docker - single service (API + DB)
# ---------------------------------------------------------------------------
docker-build:
	$(call check_env)
	@./scripts/build-docker.sh $(ENV)

docker-up:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) up -d --build db app

docker-down:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) down

docker-logs:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) logs -f app db

# ---------------------------------------------------------------------------
# Docker - full stack (API + DB + Prometheus + Grafana)
# ---------------------------------------------------------------------------
stack-up:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) up -d

stack-down:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) down

stack-logs:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) logs -f

# ---------------------------------------------------------------------------
# Railway - borrado de historial conversacional (Postgres + Redis)
# ---------------------------------------------------------------------------
# Usa los endpoints PÚBLICOS de Railway. Las URLs se leen de los servicios y se
# pasan por env al script SIN imprimirse (no quedan en pantalla ni en el shell).
RAILWAY       ?= railway
PG_SERVICE    ?= pgvector
REDIS_SERVICE ?= Redis
PYTHON_BIN    ?= ./.venv/bin/python

# Recupera las URLs públicas; falla con mensaje claro si no están disponibles.
define fetch_railway_urls
	DBURL="$$($(RAILWAY) variables --service $(PG_SERVICE) --kv 2>/dev/null | sed -n 's/^DATABASE_URL=//p')"; \
	RDURL="$$($(RAILWAY) variables --service $(REDIS_SERVICE) --kv 2>/dev/null | sed -n 's/^REDIS_PUBLIC_URL=//p')"; \
	if [ -z "$$DBURL" ] || [ -z "$$RDURL" ]; then \
		echo "No pude obtener las URLs públicas de Railway."; \
		echo "  Verifica: 'railway login', el proyecto enlazado, y los servicios '$(PG_SERVICE)' / '$(REDIS_SERVICE)'."; \
		exit 1; \
	fi
endef

# Solo cuenta lo que se borraría (no borra nada).
history-check:
	@$(fetch_railway_urls); \
	WIPE_DATABASE_URL="$$DBURL" WIPE_REDIS_URL="$$RDURL" $(PYTHON_BIN) scripts/wipe_history.py --dry-run

# Respalda y borra. CONFIRM=yes salta la pregunta. NO_BACKUP=1 omite el respaldo.
history-wipe:
	@$(fetch_railway_urls); \
	YES=""; [ "$(CONFIRM)" = "yes" ] && YES="--yes"; \
	NOBK=""; [ "$(NO_BACKUP)" = "1" ] && NOBK="--no-backup"; \
	WIPE_DATABASE_URL="$$DBURL" WIPE_REDIS_URL="$$RDURL" \
		$(PYTHON_BIN) scripts/wipe_history.py --apply $$YES $$NOBK $(if $(BACKUP_DIR),--backup-dir $(BACKUP_DIR),)

# Vacía TODAS las tablas del esquema public (TRUNCATE, no borra el esquema).
# Pasa la URL directo: make db-wipe-all DBURL='postgresql://...'  → dry-run (solo cuenta).
# Añade CONFIRM=yes para borrar de verdad. KEEP="a,b" cambia las tablas a preservar
# (por defecto alembic_version). No usa el CLI de Railway: das la URL vos.
db-wipe-all:
	@[ -n "$(DBURL)" ] || { echo "Uso: make db-wipe-all DBURL='postgresql://...' [CONFIRM=yes] [KEEP='alembic_version']"; exit 1; }
	@DB_TRUNCATE_URL="$(DBURL)" KEEP="$(KEEP)" $(PYTHON_BIN) scripts/db_truncate_all.py \
		$(if $(filter yes,$(CONFIRM)),--apply --yes,)

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
clean:
	rm -rf .venv __pycache__ .pytest_cache

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
help:
	@echo "Usage: make <target> [ENV=development|staging|production|test]"
	@echo ""
	@echo "Setup:"
	@echo "  install              Install deps, set up pre-commit hooks"
	@echo ""
	@echo "Server:"
	@echo "  dev                  Dev server with hot reload (port 8000)"
	@echo "  staging              Staging server"
	@echo "  prod                 Production server"
	@echo ""
	@echo "Database:"
	@echo "  migrate              Run migrations to latest (default ENV=development)"
	@echo "  migration MSG=...    Generate migration from model changes"
	@echo "  migrate-downgrade    Rollback last migration"
	@echo "  migrate-history      Show migration history"
	@echo ""
	@echo "Evaluation:"
	@echo "  eval                 Run evals (interactive)"
	@echo "  eval-quick           Run evals (default settings)"
	@echo "  eval-no-report       Run evals without report"
	@echo "  eval-scenarios       Run scenario evals (agent plays + LLM judge grades)"
	@echo ""
	@echo "Code quality:"
	@echo "  lint                 Ruff lint check"
	@echo "  format               Ruff format"
	@echo "  typecheck            Pyright static type check"
	@echo "  check                Run lint + typecheck"
	@echo "  pre-commit           Run all pre-commit hooks"
	@echo "  pre-commit-update    Update pre-commit hook versions"
	@echo ""
	@echo "Docker (API + DB):"
	@echo "  docker-build         Build Docker image"
	@echo "  docker-up            Start API + DB containers"
	@echo "  docker-down          Stop containers"
	@echo "  docker-logs          Tail container logs"
	@echo ""
	@echo "Docker (full stack - includes Prometheus + Grafana):"
	@echo "  stack-up             Start entire stack"
	@echo "  stack-down           Stop entire stack"
	@echo "  stack-logs           Tail all service logs"
	@echo ""
	@echo "Railway - borrar historial del agente (Postgres + Redis):"
	@echo "  history-check        Cuenta qué se borraría (no borra nada)"
	@echo "  history-wipe         Respalda y borra (CONFIRM=yes salta la pregunta; NO_BACKUP=1 omite respaldo)"
	@echo "  db-wipe-all          TRUNCATE de TODAS las tablas (DBURL='...'; CONFIRM=yes para borrar; KEEP='a,b')"
	@echo ""
	@echo "Misc:"
	@echo "  clean                Remove .venv, __pycache__, .pytest_cache"

.PHONY: install dev staging prod _serve \
        migrate migration migrate-downgrade migrate-history \
        eval eval-quick eval-no-report eval-scenarios \
        lint format typecheck check pre-commit pre-commit-update \
        docker-build docker-up docker-down docker-logs \
        stack-up stack-down stack-logs \
        history-check history-wipe db-wipe-all \
        clean help
