#!/bin/zsh
# Context Trace — Quick Action wrapper.
#
# Invoked by the macOS Quick Action with the highlighted text as $1 (Automator
# passes the selection as an argument). Runs the render pipeline.
#
# The Service environment has NO shell config, a minimal PATH, and no exported
# env vars — so everything here is absolute and self-contained. API keys come
# from config.json via config.py, not the environment.

# --- absolute paths (Service PATH won't find these) ---
PROJECT_DIR="/Users/scottjenson/Projects/Lineage"
PYTHON="/opt/homebrew/opt/python@3.14/bin/python3.14"
LOG="$PROJECT_DIR/last-run.log"

SELECTION="$1"

# render.py opens a browser "working…" page immediately and shows the result (or
# an error) there, so no notifications are needed. Output is still logged for
# debugging, since the Service discards stderr.
[[ -z "$SELECTION" ]] && exit 0

cd "$PROJECT_DIR" || exit 1
"$PYTHON" render.py "$SELECTION" >"$LOG" 2>&1
