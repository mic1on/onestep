#!/bin/sh
set -eu

WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
: "${ONESTEP_TARGET:?ONESTEP_TARGET is required}"

append_pythonpath() {
  if [ -n "${PYTHONPATH:-}" ]; then
    PYTHONPATH="$1:$PYTHONPATH"
  else
    PYTHONPATH="$1"
  fi
  export PYTHONPATH
}

append_pythonpath "$WORKSPACE_DIR"

if [ -d "$WORKSPACE_DIR/src" ]; then
  append_pythonpath "$WORKSPACE_DIR/src"
fi

cd "$WORKSPACE_DIR"

case "$ONESTEP_TARGET" in
  /*|./*|../*|*.yaml|*.yml)
    if [ ! -r "$ONESTEP_TARGET" ]; then
      echo "onestep-worker: target file is not readable: $ONESTEP_TARGET" >&2
      exit 1
    fi
    ;;
esac

echo "onestep-worker: workspace=$WORKSPACE_DIR"
echo "onestep-worker: target=$ONESTEP_TARGET"
echo "onestep-worker: pythonpath=$PYTHONPATH"

if [ -f "$WORKSPACE_DIR/requirements.txt" ]; then
  echo "onestep-worker: installing requirements from $WORKSPACE_DIR/requirements.txt"
  python -m pip install --no-build-isolation -r "$WORKSPACE_DIR/requirements.txt"
elif [ -f "$WORKSPACE_DIR/pyproject.toml" ]; then
  echo "onestep-worker: installing project from $WORKSPACE_DIR/pyproject.toml"
  python -m pip install --no-build-isolation "$WORKSPACE_DIR"
else
  echo "onestep-worker: no workspace dependency file found"
fi

echo "onestep-worker: running check for $ONESTEP_TARGET"
onestep check "$ONESTEP_TARGET"
echo "onestep-worker: starting run for $ONESTEP_TARGET"
exec onestep run "$ONESTEP_TARGET"
