#!/bin/sh
set -eu

BASE_DIR="/share/ZFS25_DATA/Operations/RD/QMS/HR/Programs/New Hiring/agenticGpt"
SCRIPT_DIR="$BASE_DIR/Hiring Automation"
DOCKER_IMAGE="${QNAP_NODE_IMAGE:-node:20-bookworm-slim}"
MARKER_FILE="$SCRIPT_DIR/node_modules/.linux-canvas-ready"

exec docker run --rm \
  -v "$BASE_DIR:$BASE_DIR" \
  -w "$SCRIPT_DIR" \
  "$DOCKER_IMAGE" \
  sh -lc '
    set -eu
    if [ ! -f "'"$MARKER_FILE"'" ]; then
      npm install --no-audit --no-fund @napi-rs/canvas >/tmp/evaluate_candidates_npm.log 2>&1 || {
        cat /tmp/evaluate_candidates_npm.log
        exit 1
      }
      : > "'"$MARKER_FILE"'"
    fi
    node ./src/evaluateCandidates.js
  '
