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
	@echo "  make all        - run full flow (validate -> placer -> eco -> cts)"
	@echo "  make placer     - run placement (use DESIGN=<name> to specify design, default: 6502)"
	@echo "  make cts        - run src/cts.py"
	@echo "  make eco        - run src/eco_generator.py"
	@echo "  make validate   - run validation (use DESIGN=<name> to specify design, default: aes_128)"
	@echo "  make visualize  - run visualization (use DESIGN=<name> to specify design)"
	@echo "  make clean      - remove __pycache__ and *.pyc files"
	@echo ""
	@echo "Examples:"
	@echo "  make placer DESIGN=6502    - Run placement for 6502 design"
	@echo "  make validate DESIGN=arith - Validate arith design"

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
	$(PY) -m src.placement.placer $(if $(DESIGN),$(DESIGN),6502)

# Run clock tree synthesis visualization
cts: install
	$(PY) -m src.cts.htree_builder $(if $(DESIGN),$(DESIGN),aes_128) --skip_verilog
	$(PY) -m src.Visualization.cts_plotter cts \
		--placement build/$(if $(DESIGN),$(DESIGN),aes_128)/$(if $(DESIGN),$(DESIGN),aes_128)_placement.csv \
		--cts_data build/$(if $(DESIGN),$(DESIGN),aes_128)/$(if $(DESIGN),$(DESIGN),aes_128)_cts.json \
		--fabric_cells inputs/Platform/fabric_cells.yaml \
		--output build/$(if $(DESIGN),$(DESIGN),aes_128)/cts_visualization.html \
		--design $(if $(DESIGN),$(DESIGN),aes_128)

# Run ECO generator
eco: install
	$(PY) -m src.eco_generator $(if $(DESIGN),$(DESIGN),aes_128)

# Run design validator
validate: install
	$(PY) -m src.validation.validator $(if $(DESIGN),$(DESIGN),aes_128)

# Run visualization (also sees DESIGN if set)
visualize: install
	DESIGN=$(DESIGN) $(PY) -m src.Visualization.sasics_visualisation

# Handle lowercase design variable
ifdef design
    DESIGN := $(design)
endif

# Run full flow: validate -> placer -> eco
all:  validate placer eco cts

# Phase 1: run both validate and visualize
phase1: validate visualize

# Cleanup Python caches
clean:
	find . -name "__pycache__" -type d -exec rm -rf {} + -o -name "*.pyc" -delete
	rm -rf $(VENV)



