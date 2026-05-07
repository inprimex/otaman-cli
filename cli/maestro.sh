#!/usr/bin/env bash
# Wrapper to invoke the maestro CLI.
#
# Resolves a Python interpreter, verifies core deps are importable by THAT
# interpreter (avoids "pip installed it but python3 can't see it" confusion),
# then execs maestro.py.
set -u

# Resolve symlinks so `ln -s maestro.sh ~/.local/bin/maestro` works.
# Portable across Linux + macOS (readlink -f is GNU-only).
source="${BASH_SOURCE[0]}"
while [[ -L "$source" ]]; do
    dir="$(cd -P "$(dirname "$source")" && pwd)"
    source="$(readlink "$source")"
    [[ "$source" != /* ]] && source="$dir/$source"
done
SCRIPT_DIR="$(cd -P "$(dirname "$source")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Pick an interpreter, honoring MAESTRO_PYTHON if set.
if [[ -n "${MAESTRO_PYTHON:-}" ]]; then
    PY="$MAESTRO_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v py >/dev/null 2>&1; then
    PY="py -3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
else
    echo "maestro: no Python interpreter found on PATH" >&2
    echo "maestro: install Python 3.10+ or set MAESTRO_PYTHON to your interpreter" >&2
    exit 127
fi

# Fail fast if PyYAML isn't importable by THIS python — the most common
# footgun is `pip3 install pyyaml` landing in a different Python.
if ! ${PY} -c "import yaml" 2>/dev/null; then
    PY_PATH="$(${PY} -c 'import sys; print(sys.executable)' 2>/dev/null || echo '<unknown>')"
    echo "maestro: PyYAML is not importable by $PY ($PY_PATH)" >&2
    echo "maestro: install into the SAME interpreter:" >&2
    echo "           ${PY} -m pip install --user -r $PLUGIN_ROOT/requirements.txt" >&2
    echo "maestro:   (add --break-system-packages on PEP 668 / Ubuntu 23.04+)" >&2
    echo "maestro: or set MAESTRO_PYTHON to an interpreter that has PyYAML" >&2
    exit 1
fi

exec ${PY} "$SCRIPT_DIR/maestro.py" "$@"
