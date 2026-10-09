# Fakee — common tasks.
# On Windows, run these from Git Bash (or just use ./scripts/start-backend.sh).

VENV_PY := $(shell if [ -x backend/.venv/Scripts/python.exe ]; then echo $(CURDIR)/backend/.venv/Scripts/python.exe; else echo $(CURDIR)/backend/.venv/bin/python; fi)

.DEFAULT_GOAL := help

.PHONY: help venv install backend dev test migrate eval frontend typecheck build extension-zip extension-screenshots check-bridge docker-up docker-down logs clean

help: ## Show this help
	@echo "Targets:"
	@echo "  venv         Create backend/.venv"
	@echo "  install      Install backend + frontend dependencies"
	@echo "  backend      Run the backend (foreground)"
	@echo "  dev          Run the backend with auto-reload"
	@echo "  test         Run the backend test suite"
	@echo "  migrate      Apply database migrations (alembic upgrade head)"
	@echo "  eval         Run the accuracy harness"
	@echo "  frontend     Run the React dev server"
	@echo "  typecheck    Typecheck the frontend"
	@echo "  build        Build the frontend for production"
	@echo "  extension-zip  Package the extension for the Chrome Web Store"
	@echo "  extension-screenshots  Render the store screenshots (needs Chrome)"
	@echo "  check-bridge Verify the website/extension bridge contract"
	@echo "  docker-up    Build and start via docker compose"
	@echo "  docker-down  Stop docker compose"
	@echo "  logs         Tail the backend log"
	@echo "  clean        Remove local build/test artifacts"

venv: ## Create the backend virtualenv
	cd backend && python -m venv .venv

install: ## Install backend + frontend dependencies
	cd backend && $(VENV_PY) -m pip install -r requirements-dev.txt
	cd frontend && npm install

backend: ## Run the backend (foreground)
	./scripts/start-backend.sh

dev: ## Run the backend with auto-reload
	RELOAD=1 ./scripts/start-backend.sh

test: ## Run the backend tests
	cd backend && $(VENV_PY) -m pytest -q

migrate: ## Apply database migrations
	cd backend && $(VENV_PY) -m alembic upgrade head

eval: ## Run the accuracy harness (precision/recall gate)
	cd backend && $(VENV_PY) -m scripts.evaluate

frontend: ## Run the React dev server
	cd frontend && npm run dev

typecheck: ## Typecheck the frontend
	cd frontend && npm run typecheck

build: ## Build the frontend
	cd frontend && npm run build

extension-zip: ## Package the extension for the Chrome Web Store
	# Pass the deployed services, e.g.:
	#   make extension-zip EXT_ARGS="--api-base https://api.example.com/api --site-url https://example.com"
	$(VENV_PY) scripts/package_extension.py $(EXT_ARGS)

extension-screenshots: ## Render the 1280x800 Chrome Web Store screenshots (needs Chrome)
	node extension/tools/make-screenshots.mjs

check-bridge: ## Verify the website/extension bridge protocol still matches
	node scripts/check-extension-bridge.mjs

docker-up: ## Start everything with docker compose
	docker compose up --build -d

docker-down: ## Stop docker compose
	docker compose down

logs: ## Tail the dev backend log
	tail -f /tmp/backend.log

clean: ## Remove local build/test artifacts
	rm -rf frontend/dist backend/.pytest_cache
	find backend -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -f backend/data/*.db
