default:
    @just --list

set dotenv-load := true

core-dir := env("OMNIGENT_CORE_DIR", "../omnigent")
sdk-dir := env("MUSE_CODE_SDK_DIR", "../muse-code-sdk")
registry-config := env(
    "OMNIGENT_PYPI_CONFIG",
    "../../Databricks/databricks-field-eng/scaling-cool-cucumbers/pyproject.toml",
)

# Create/update this package's development environment.
[group('setup')]
ensure:
    #!/usr/bin/env bash
    set -euo pipefail
    index_url="${UV_INDEX_URL:-}"
    if [[ -z "$index_url" && -f "{{registry-config}}" ]]; then
        index_url="$(.venv/bin/python -c 'import sys, tomllib; data = tomllib.load(open(sys.argv[1], "rb")); print(next(index["url"] for index in data["tool"]["uv"]["index"] if index.get("default")))' "{{registry-config}}")"
    fi
    if [[ -z "$index_url" ]]; then
        echo "No package index configured. Set UV_INDEX_URL in .env or OMNIGENT_PYPI_CONFIG to a pyproject with a default uv index." >&2
        exit 2
    fi
    OMNIGENT_SKIP_WEB_UI=true UV_INDEX_URL="$index_url" uv sync --group dev

# Run the offline unit suite.
[group('check')]
test:
    uv run --no-sync pytest -q

# Check Python style without modifying files.
[group('check')]
lint:
    uv run --no-sync ruff check .
    uv run --no-sync ruff format --check .
    uv run --no-sync pyrefly check

# Run all fast local checks.
[group('check')]
check: lint test

# Apply Ruff's safe fixes and formatter.
[group('check')]
format:
    uv run --no-sync ruff check --fix .
    uv run --no-sync ruff format .

# Create/update the sibling Omnigent development environment.
[group('integration')]
core-ensure:
    #!/usr/bin/env bash
    set -euo pipefail
    index_url="${UV_INDEX_URL:-}"
    if [[ -z "$index_url" && -f "{{registry-config}}" ]]; then
        index_url="$(.venv/bin/python -c 'import sys, tomllib; data = tomllib.load(open(sys.argv[1], "rb")); print(next(index["url"] for index in data["tool"]["uv"]["index"] if index.get("default")))' "{{registry-config}}")"
    fi
    if [[ -z "$index_url" ]]; then
        echo "No package index configured. Set UV_INDEX_URL in .env or OMNIGENT_PYPI_CONFIG to a pyproject with a default uv index." >&2
        exit 2
    fi
    cd {{core-dir}}
    UV_INDEX_URL="$index_url" uv sync --extra all --group dev

# Install this plugin and the local Muse SDK into the sibling Omnigent venv.
[group('integration')]
core-install: core-ensure
    cd {{core-dir}} && uv pip install --python .venv/bin/python \
        -e {{justfile_directory()}} \
        -e {{justfile_directory()}}/{{sdk-dir}}/python/clients/sdk-py \
        -e {{justfile_directory()}}/{{sdk-dir}}/python/clients/msp-py

# Launch Omnigent's full development pod. Press R after plugin edits.
[group('integration')]
dev: core-install
    cd {{core-dir}} && just dev
