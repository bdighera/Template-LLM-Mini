#!/bin/sh
# Runs scripts/workflow.py with the first working Python 3.9+ for hooks, which cannot
# assume `python3`: Windows often has only `python` or `py`, or a Microsoft Store stub
# that exists on PATH but does not run. WORKFLOW_PYTHON overrides the search.
script="$(dirname "$0")/workflow.py"
for candidate in "$WORKFLOW_PYTHON" python3 python py; do
    [ -n "$candidate" ] || continue
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
        exec "$candidate" "$script" "$@"
    fi
done
echo 'workflow.sh: no Python 3.9+ found (tried python3, python, py); set WORKFLOW_PYTHON' >&2
exit 2  # Fail closed: Claude Code blocks the tool call and Git blocks the commit.
