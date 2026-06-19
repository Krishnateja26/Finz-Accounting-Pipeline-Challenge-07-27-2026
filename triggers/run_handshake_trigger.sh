#!/bin/sh
set -eu

BASE_DIR="/share/ZFS25_DATA/Operations/RD/QMS/HR/Programs/New Hiring/agenticGpt"
SCRIPT_DIR="$BASE_DIR/Handshake Resume Automation/Handshake Automation Script version 2"
SCRIPT_PATH="$SCRIPT_DIR/Handshake Automation with Email v2.1.py"
DOCKER_IMAGE="${QNAP_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.60.0-jammy}"
RUNTIME_PACKAGES="$BASE_DIR/triggers/runtime/python-packages"
PLAYWRIGHT_BROWSERS="$BASE_DIR/triggers/runtime/playwright-browsers"
PROFILE_DIR="$BASE_DIR/triggers/runtime/handshake-profile"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_BACKUP="$SCRIPT_DIR/.env.nas-trigger.bak"
MARKER_FILE="$PLAYWRIGHT_BROWSERS/.chrome-ready"

exec docker run --rm \
  -v "$BASE_DIR:$BASE_DIR" \
  -w "$SCRIPT_DIR" \
  "$DOCKER_IMAGE" \
  sh -lc '
    set -eu
    mkdir -p "'"$RUNTIME_PACKAGES"'"
    mkdir -p "'"$PLAYWRIGHT_BROWSERS"'"
    rm -rf "'"$PROFILE_DIR"'"
    mkdir -p "'"$PROFILE_DIR"'"
    cp "'"$ENV_FILE"'" "'"$ENV_BACKUP"'"
    trap '"'"'if [ -f "'"$ENV_BACKUP"'" ]; then mv -f "'"$ENV_BACKUP"'" "'"$ENV_FILE"'"; fi'"'"' EXIT INT TERM
    python3 - <<'"'"'PY'"'"'
from pathlib import Path

env_path = Path(r"'"$ENV_FILE"'")
lines = env_path.read_text(encoding="utf-8").splitlines()
updated = []
seen_headless = False
seen_profile = False

for line in lines:
    if line.startswith("HEADLESS="):
        if not seen_headless:
            updated.append("HEADLESS=true")
            seen_headless = True
    elif line.startswith("PROFILE_DIR="):
        if not seen_profile:
            updated.append("PROFILE_DIR=/share/ZFS25_DATA/Operations/RD/QMS/HR/Programs/New Hiring/agenticGpt/triggers/runtime/handshake-profile")
            seen_profile = True
    else:
        updated.append(line)

if not seen_headless:
    updated.append("HEADLESS=true")
if not seen_profile:
    updated.append("PROFILE_DIR=/share/ZFS25_DATA/Operations/RD/QMS/HR/Programs/New Hiring/agenticGpt/triggers/runtime/handshake-profile")

env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
    if [ ! -f "'"$MARKER_FILE"'" ]; then
      python3 -m pip install --no-cache-dir --target "'"$RUNTIME_PACKAGES"'" playwright openpyxl >/tmp/handshake_pip.log 2>&1 || {
        cat /tmp/handshake_pip.log
        exit 1
      }
      export PYTHONPATH="'"$RUNTIME_PACKAGES"'${PYTHONPATH:+:$PYTHONPATH}"
      PLAYWRIGHT_BROWSERS_PATH="'"$PLAYWRIGHT_BROWSERS"'" python3 -m playwright install chrome >/tmp/handshake_browser.log 2>&1 || {
        cat /tmp/handshake_browser.log
        exit 1
      }
      CHROME_BIN="$(find "'"$PLAYWRIGHT_BROWSERS"'" /ms-playwright /root/.cache/ms-playwright -type f \( -name chrome -o -name google-chrome \) 2>/dev/null | head -n 1 || true)"
      if [ -z "$CHROME_BIN" ]; then
        echo "Could not locate Chrome binary after Playwright install." >&2
        exit 1
      fi
      mkdir -p /opt/google/chrome
      ln -sf "$CHROME_BIN" /opt/google/chrome/chrome
      : > "'"$MARKER_FILE"'"
    fi
    CHROME_BIN="$(find "'"$PLAYWRIGHT_BROWSERS"'" /ms-playwright /root/.cache/ms-playwright -type f \( -name chrome -o -name google-chrome \) 2>/dev/null | head -n 1 || true)"
    if [ -n "$CHROME_BIN" ]; then
      mkdir -p /opt/google/chrome
      ln -sf "$CHROME_BIN" /opt/google/chrome/chrome
    fi
    export PYTHONPATH="'"$RUNTIME_PACKAGES"'${PYTHONPATH:+:$PYTHONPATH}"
    export PLAYWRIGHT_BROWSERS_PATH="'"$PLAYWRIGHT_BROWSERS"'"
    export PROFILE_DIR="'"$PROFILE_DIR"'"
    export HEADLESS=true
    python3 "'"$SCRIPT_PATH"'"
  '
