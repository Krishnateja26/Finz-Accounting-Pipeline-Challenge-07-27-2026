#!/bin/sh
set -eu

BASE_DIR="/share/ZFS25_DATA/Operations/RD/QMS/HR/Programs/New Hiring/agenticGpt"
SCRIPT_DIR="$BASE_DIR/resume parser"
SCRIPT_PATH="$SCRIPT_DIR/resume_indexer.py"
PYTHON_BIN="/share/ZFS530_DATA/.qpkg/QKVM/usr/bin/python3"
RUNTIME_BIN="$BASE_DIR/triggers/runtime/bin"
PYTHON_PACKAGES="$BASE_DIR/triggers/runtime/python-packages"

cd "$SCRIPT_DIR"
export LD_LIBRARY_PATH="/share/ZFS530_DATA/.qpkg/QKVM/usr/lib"
export PYTHONHOME="/share/ZFS530_DATA/.qpkg/QKVM/usr"
export PYTHONPATH="$PYTHON_PACKAGES${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$RUNTIME_BIN:$PATH"

exec "$PYTHON_BIN" "$SCRIPT_PATH"
