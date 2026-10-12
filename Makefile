.PHONY: install reproduce test lint check

install:                ## install the pipeline with test, figure and labeling-app dependencies
	pip install -e ".[dev,paper,label]"

reproduce:              ## regenerate every number and figure, and verify them
	scripts/reproduce.sh

test:
	pytest

lint:
	ruff check .
	ruff format --check .
	mypy

check: lint test
