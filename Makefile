PYTHON ?= .venv/bin/python
BOOTSTRAP_PYTHON ?= python3

.PHONY: help setup contracts

help:
	@echo "make setup      Create local Python environment for contract validation"
	@echo "make contracts  Validate REST contracts, schemas, and examples"

setup:
	$(BOOTSTRAP_PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r api_contracts/tools/requirements.txt

contracts:
	$(PYTHON) api_contracts/tools/validate_contracts.py
