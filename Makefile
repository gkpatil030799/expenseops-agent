PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

install:
	$(PYTHON) -m pip install -e ".[dev]"

run:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

start:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port $${PORT:-8000}

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check app tests
