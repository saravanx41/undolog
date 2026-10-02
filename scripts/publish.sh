#!/usr/bin/env bash
# Publish undolog to PyPI in dependency order.
# Prereq: ~/.pypirc exists with a PyPI API token (see launch/checklist.md).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f "$HOME/.pypirc" ]; then
    echo "ERROR: ~/.pypirc not found. Create it first (see launch/checklist.md):" >&2
    echo '  [pypi]' >&2
    echo '  username = __token__' >&2
    echo '  password = pypi-YOUR_TOKEN_HERE' >&2
    exit 1
fi

PY="$(pwd)/.venv/bin/python"
$PY -m twine --version >/dev/null 2>&1 || uv pip install --python "$PY" twine

publish() {
    local dir="$1"
    echo "=== building $dir ==="
    (cd "$dir" && rm -rf dist && uv build -q)
    $PY -m twine check "$dir"/dist/*
    echo "=== uploading $dir ==="
    $PY -m twine upload -r pypi "$dir"/dist/*
}

# Dependency order: core → adapters/langgraph/torture → meta package last.
publish packages/core
publish packages/adapters
publish packages/langgraph
publish packages/torture
publish .

echo "=== done. Stranger smoke test: ==="
echo "  uv venv /tmp/stranger && uv pip install --python /tmp/stranger/bin/python undolog"
echo "  docker compose up -d && /tmp/stranger/bin/undolog demo"
