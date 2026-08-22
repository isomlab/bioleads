#!/bin/bash
# bioleads — double-click launcher (macOS).
#
# First run: creates the 'bioleads' conda environment from environment.yml
# (Python + the app + its dependencies); this can take a few minutes.
# Every run after that: just opens the app.
#
# Requirement: install Miniforge once (a normal clickable installer):
#   https://conda-forge.org/download/

ENV_NAME="bioleads"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"   # this script lives in <repo>/launchers/

pause_and_exit() {
    echo
    read -r -p "Press Return to close this window…" _
    exit "${1:-1}"
}

find_conda() {
    local c
    for c in "$HOME/miniforge3/bin/conda" "$HOME/mambaforge/bin/conda" \
             "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" \
             "/opt/homebrew/Caskroom/miniforge/base/bin/conda" \
             "$(command -v conda 2>/dev/null)"; do
        if [ -n "$c" ] && [ -x "$c" ]; then
            echo "$c"; return 0
        fi
    done
    return 1
}

CONDA="$(find_conda)" || {
    echo "Could not find conda on this Mac."
    echo "Please install Miniforge first (clickable installer):"
    echo "    https://conda-forge.org/download/"
    pause_and_exit 1
}

# Create the environment the first time only.
if ! "$CONDA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "First-time setup: creating the '$ENV_NAME' environment (a few minutes)…"
    echo
    ( cd "$REPO" && "$CONDA" env create -f environment.yml ) || {
        echo
        echo "Setup did not finish. Please see the messages above."
        pause_and_exit 1
    }
    echo
    echo "Setup complete."
fi

# --- keep this copy current -------------------------------------------------
# Best-effort throughout: an offline laptop, or a clone with local edits, still
# launches on the code it already has.

update_repo() {
    command -v git >/dev/null 2>&1 || return 0
    git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
    git -C "$REPO" remote get-url origin >/dev/null 2>&1 || return 0
    git -C "$REPO" symbolic-ref -q HEAD >/dev/null 2>&1 || return 0   # detached
    if [ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ]; then
        echo "This copy has local changes — skipping update."
        return 0
    fi
    echo "Checking for updates…"
    local before after
    before="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)"
    if ! git -C "$REPO" pull --ff-only --quiet 2>/dev/null; then
        echo "  could not reach the server — launching the copy you have."
        return 0
    fi
    after="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)"
    if [ "$before" = "$after" ]; then echo "  already up to date."; else echo "  updated."; fi
}

# The app is installed editable, so new code needs no reinstall — a new
# dependency does. Rebuild only when environment.yml is newer than the env,
# which also covers a pull done by hand outside this launcher.
update_env() {
    local prefix yml
    yml="$REPO/environment.yml"
    [ -f "$yml" ] || return 0
    prefix="$("$CONDA" env list | awk -v n="$ENV_NAME" '$1 == n {print $NF}')"
    [ -n "$prefix" ] && [ -f "$prefix/conda-meta/history" ] || return 0
    if [ "$yml" -nt "$prefix/conda-meta/history" ]; then
        echo "Dependencies changed — updating the '$ENV_NAME' environment…"
        ( cd "$REPO" && "$CONDA" env update -f environment.yml ) \
            || echo "  update failed — launching on the environment you have."
    fi
}

update_repo
update_env

echo "Starting bioleads…"
# Isolate from the user's Python environment: PYTHONPATH is cleared (entries there
# take precedence over the env's site-packages and can shadow the app's package),
# and we run from a neutral directory. The created environment is self-contained.
cd "$HOME" || cd /
exec env -u PYTHONPATH "$CONDA" run --no-capture-output -n "$ENV_NAME" bioleads-gui
