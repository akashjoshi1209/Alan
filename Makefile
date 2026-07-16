# Alan - Personal AI Operating System
# Makefile for common development tasks

.PHONY: help install install-dev install-all lint format typecheck test test-unit test-integration test-e2e coverage clean migrate upgrade-db seed shell run run-api run-cli run-mcp docker-build docker-run docker-stop docs

# Default target
help:
	@echo "Alan - Personal AI Operating System"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Setup:"
	@echo "  install        Install production dependencies"
	@echo "  install-dev    Install development dependencies"
	@echo "  install-all    Install all optional dependencies"
	@echo "  setup          Run initial setup (create dirs, migrate DB, seed data)"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint           Run ruff linter"
	@echo "  format         Format code with black and ruff"
	@echo "  typecheck      Run mypy type checker"
	@echo "  check          Run all code quality checks"
	@echo "  pre-commit     Install and run pre-commit hooks"
	@echo ""
	@echo "Testing:"
	@echo "  test           Run all tests"
	@echo "  test-unit      Run unit tests only"
	@echo "  test-integration  Run integration tests only"
	@echo "  test-e2e       Run end-to-end tests only"
	@echo "  coverage       Run tests with coverage report"
	@echo ""
	@echo "Database:"
	@echo "  migrate        Run database migrations"
	@echo "  migrate-create Create new migration"
	@echo "  upgrade-db     Upgrade database to latest revision"
	@echo "  downgrade-db   Downgrade database by one revision"
	@echo "  seed           Seed database with sample data"
	@echo "  shell          Open database shell"
	@echo ""
	@echo "Running:"
	@echo "  run            Run Alan CLI"
	@echo "  run-api        Run FastAPI server"
	@echo "  run-cli        Run interactive CLI"
	@echo "  run-mcp        Run MCP server"
	@echo ""
	@echo "Docker:"
	@echo "  docker-build   Build Docker image"
	@echo "  docker-run     Run Docker container"
	@echo "  docker-stop    Stop Docker container"
	@echo "  docker-logs    View Docker logs"
	@echo ""
	@echo "Documentation:"
	@echo "  docs           Generate documentation"
	@echo "  docs-serve     Serve documentation locally"
	@echo ""
	@echo "Maintenance:"
	@echo "  clean          Clean build artifacts and caches"
	@echo "  deps-check     Check for outdated dependencies"
	@echo "  deps-update    Update dependencies"

# =============================================================================
# Setup
# =============================================================================

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

install-all:
	pip install -e ".[full]"

setup: install-dev
	@mkdir -p data/chromadb logs
	@cp -n .env.example .env || true
	@make migrate
	@make seed
	@echo "Setup complete! Run 'make run' to start Alan."

# =============================================================================
# Code Quality
# =============================================================================

lint:
	ruff check alan tests scripts

format:
	ruff format alan tests scripts
	ruff check --fix alan tests scripts

typecheck:
	mypy alan

check: lint typecheck

pre-commit:
	pre-commit install
	pre-commit run --all-files

# =============================================================================
# Testing
# =============================================================================

test:
	pytest

test-unit:
	pytest -m "unit"

test-integration:
	pytest -m "integration"

test-e2e:
	pytest -m "e2e"

coverage:
	pytest --cov=alan --cov-report=html --cov-report=term-missing
	@echo "Coverage report generated in htmlcov/"

# =============================================================================
# Database
# =============================================================================

migrate:
	alembic revision --autogenerate -m "$(MSG)"

migrate-create:
	@read -p "Migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"

upgrade-db:
	alembic upgrade head

downgrade-db:
	alembic downgrade -1

seed:
	python -m alan.scripts.seed

shell:
	alembic history
	@echo "Use 'make shell-db' for SQL shell"

shell-db:
	psql $(DATABASE_URL)

# =============================================================================
# Running
# =============================================================================

run:
	python -m alan

run-api:
	uvicorn alan.api.main:create_app --host 0.0.0.0 --port 8000 --reload

run-cli:
	python -m alan chat

run-mcp:
	python -m alan mcp serve

run-voice:
	python -m alan voice start

# =============================================================================
# Docker
# =============================================================================

docker-build:
	docker build -t alan:latest .

docker-run:
	docker run -d \
		--name alan \
		-p 8000:8000 \
		-v $(PWD)/data:/app/data \
		-v $(PWD)/.env:/app/.env \
		alan:latest

docker-stop:
	docker stop alan && docker rm alan

docker-logs:
	docker logs -f alan

docker-shell:
	docker exec -it alan bash

# =============================================================================
# Documentation
# =============================================================================

docs:
	python -m alan.scripts.generate_docs

docs-serve:
	cd docs && python -m http.server 8080

# =============================================================================
# Maintenance
# =============================================================================

clean:
	rm -rf build/ dist/ *.egg-info/
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ htmlcov/
	rm -rf __pycache__/ alan/__pycache__/ alan/*/__pycache__/ alan/*/*/__pycache__/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyd" -delete

deps-check:
	pip list --outdated

deps-update:
	pip install --upgrade pip
	pip install --upgrade -r requirements.txt

# =============================================================================
# Development Helpers
# =============================================================================

# Generate requirements.txt from pyproject.toml
requirements:
	pip-compile pyproject.toml -o requirements.txt

# Check for security vulnerabilities
security-check:
	pip-audit
	bandit -r alan

# Profile performance
profile:
	python -m cProfile -o profile.stats -m alan chat
	python -c "import pstats; p = pstats.Stats('profile.stats'); p.sort_stats('cumulative').print_stats(30)"

# Run benchmarks
benchmark:
	python -m alan.scripts.benchmark

# Generate OpenAPI spec
openapi:
	python -c "from alan.api.main import create_app; import json; app = create_app(); print(json.dumps(app.openapi(), indent=2))" > openapi.json

# =============================================================================
# CI/CD Helpers
# =============================================================================

ci-lint:
	ruff check --output-format=github alan tests scripts

ci-format:
	ruff format --check alan tests scripts

ci-typecheck:
	mypy alan --junit-xml=mypy-report.xml

ci-test:
	pytest --junitxml=test-results.xml --cov=alan --cov-report=xml

ci-all: ci-lint ci-format ci-typecheck ci-test

# Release preparation
release-check:
	@echo "Checking release readiness..."
	@make clean
	@make check
	@make test
	@echo "All checks passed!"

# =============================================================================
# Default
# =============================================================================

.DEFAULT_GOAL := help