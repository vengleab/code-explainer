.PHONY: dev backend frontend install build test help

# ── Default ────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  make dev       — run backend (port 3000) + frontend (port 5173) together"
	@echo "  make backend   — run only the Python API server"
	@echo "  make frontend  — run only the Vite dev server"
	@echo "  make install   — install frontend npm dependencies"
	@echo "  make build     — production build of the frontend"
	@echo "  make test      — run the backend unit tests (stdlib unittest)"
	@echo ""

# ── Run both concurrently ──────────────────────────────────────────────
# Uses a trap so Ctrl+C kills both child processes cleanly.
dev:
	@echo "Starting backend (port 3000) and frontend (port 5173)…"
	@trap 'kill 0' INT; \
	  ( source .venv/bin/activate && python3 dev_server.py ) & \
	  ( cd frontend && npm run dev ) & \
	  wait

# ── Individual targets ─────────────────────────────────────────────────
backend:
	source .venv/bin/activate && python3 dev_server.py

frontend:
	cd frontend && npm run dev

# ── Setup / build ──────────────────────────────────────────────────────
install:
	cd frontend && npm install

build:
	cd frontend && npm run build

# ── Tests ──────────────────────────────────────────────────────────────
# Prefers the repo .venv (has Pillow/pandas); falls back to python3.
test:
	@PY=.venv/bin/python; [ -x "$$PY" ] || PY=python3; \
	  echo "Running backend tests with $$PY"; \
	  $$PY -m unittest discover -s backend/tests -v
