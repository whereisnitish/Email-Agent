.DEFAULT_GOAL := help
PYTHON ?= python

.PHONY: help install dev lint fmt typecheck test coverage check demo serve watch build docker clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package
	$(PYTHON) -m pip install -e .

dev:  ## Install with the development extras
	$(PYTHON) -m pip install -e ".[dev]"

lint:  ## Run ruff
	$(PYTHON) -m ruff check .

fmt:  ## Fix what ruff can fix automatically
	$(PYTHON) -m ruff check --fix .

typecheck:  ## Run mypy (advisory; not gated in CI)
	$(PYTHON) -m mypy

test:  ## Run the test suite
	$(PYTHON) -m unittest discover -s tests -v

coverage:  ## Run tests with coverage reporting
	$(PYTHON) -m coverage run -m unittest discover -s tests
	$(PYTHON) -m coverage report

check: lint test  ## Lint and test - run this before opening a PR

demo:  ## Extract every bundled sample email
	$(PYTHON) -m email_intent.cli --demo --format table

serve:  ## Start the webhook listener on :8080
	$(PYTHON) -m email_intent.server --serve 8080

watch:  ## Watch ./inbox for new email files
	$(PYTHON) -m email_intent.server --watch ./inbox --output ./extracted

build:  ## Build the wheel and sdist
	$(PYTHON) -m build

docker:  ## Build the container image
	docker build -t email-intent:latest .

clean:  ## Remove build artefacts and caches
	rm -rf dist build *.egg-info src/*.egg-info .coverage htmlcov .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
