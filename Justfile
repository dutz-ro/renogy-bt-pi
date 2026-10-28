# Renogy Monitor — common tasks (run from repo root)

set dotenv-load := false

pi_dir := "pi"

default:
    @just --list

# Install package + dev deps (laptop / CI)
install:
    cd {{pi_dir}} && pip install -e ".[dev]"

# Unit tests — no hardware
test:
    cd {{pi_dir}} && pytest -q

# ruff + mypy --strict
lint:
    cd {{pi_dir}} && ruff check src tests && mypy --strict src

# Format
fmt:
    cd {{pi_dir}} && ruff format src tests && ruff check --fix src tests

# Deploy to Pi via Tailscale (set PI_HOST)
deploy host=env_var_or_default('PI_HOST', 'renogy-pi'):
    rsync -avz --delete \
        --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
        --exclude '*.egg-info' --exclude '.mypy_cache' --exclude '.ruff_cache' \
        {{pi_dir}}/ {{host}}:~/renogy-monitor/
    ssh {{host}} 'cd ~/renogy-monitor && pip install -e . --quiet && sudo systemctl restart renogy-monitor || true'
