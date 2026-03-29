PYTHON ?= python
PIP ?= pip

VENV_DIR ?= .venv
ACTIVATE = . $(VENV_DIR)/bin/activate

.PHONY: help venv install playwright test lint run-search

help:
	@echo "linkedin_copilot helper commands"
	@echo "  make venv         - create virtual environment"
	@echo "  make install      - install dependencies"
	@echo "  make playwright   - install Playwright browsers"
	@echo "  make test         - run tests"
	@echo "  make run-search   - example search run"

venv:
	$(PYTHON) -m venv $(VENV_DIR)

install: venv
	$(ACTIVATE) && $(PIP) install -e .

playwright:
	$(ACTIVATE) && $(PYTHON) -m playwright install

test:
	$(ACTIVATE) && $(PYTHON) -m pytest -q

run-search:
	$(ACTIVATE) && $(PYTHON) -m linkedin_copilot search --keywords "python backend" --location "Israel" --easy-apply

