# Makefile for Structured-ASIC-Project

PYTHON      ?= python3
VENV        := .venv
PIP         := $(VENV)/bin/pip
PY          := $(VENV)/bin/python

.PHONY: help venv install parsers placer cts eco validate visualize clean

# Default target
help:
	@echo "Available targets:"
	@echo "  make venv       - create local virtualenv (.venv)"
	@echo "  make install    - install Python dependencies from requirements.txt"
	@echo "  make parsers    - run all parsers in src/parsers/"
	@echo "  make placer     - run src/placer.py"
	@echo "  make cts        - run src/cts.py"
	@echo "  make eco        - run src/eco_generator.py"
	@echo "  make validate   - run src/validation/validator.py"
	@echo "  make visualize  - run src/Visualization/sasics_visualisation.py"
	@echo "  make clean      - remove __pycache__ and *.pyc files"

# Create virtual environment
venv:
	$(PYTHON) -m venv $(VENV)

# Install dependencies into the venv
install: venv
	$(PIP) install -r requirements.txt

# Run all parser scripts
parsers: install
	$(PY) -m src.parsers.fabric_db
	$(PY) -m src.parsers.fabric_cells_parser
	$(PY) -m src.parsers.fabric_parser
	$(PY) -m src.parsers.netlist_parser
	$(PY) -m src.parsers.pins_parser

# Run placer
placer: install
	$(PY) -m src.placer

# Run clock tree synthesis
cts: install
	$(PY) -m src.cts

# Run ECO generator
eco: install
	$(PY) -m src.eco_generator

# Run design validator
validate: install
	$(PY) -m src.validation.validator

# Run visualization (also sees DESIGN if set)
visualize: install
	DESIGN=$(DESIGN) $(PY) -m src.Visualization.sasics_visualisation

# Phase 1: run both validate and visualize
phase1: validate visualize

# Cleanup Python caches
clean:
	find . -name "__pycache__" -type d -exec rm -rf {} + -o -name "*.pyc" -delete
	rm -rf $(VENV)



