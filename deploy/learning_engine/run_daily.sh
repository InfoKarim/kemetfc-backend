#!/usr/bin/env bash
# KEMET learning engine: one daily run. NOT installed or enabled by anything in this repository.
#
# Required environment (set by the scheduler's own configuration, never in this file):
#   KEMET_LE_REPO    absolute path to a checkout of the repository
#   KEMET_LE_CONFIG  absolute path to the private config JSON (starts from learning_engine/config.example.json)
#   KEMET_LE_STATE   absolute path to the private state directory (NOT the application's data)
# API keys, if you ever enable live mode, come from the environment (for example launchd's EnvironmentVariables or a
# root-owned EnvironmentFile for systemd), never from this file or the config.
#
# The run is OFFLINE unless the config says mode "live" AND network_enabled true AND a channel allowlist and key exist.
set -euo pipefail
umask 077
export TZ=America/Detroit
: "${KEMET_LE_REPO:?set KEMET_LE_REPO}" "${KEMET_LE_CONFIG:?set KEMET_LE_CONFIG}" "${KEMET_LE_STATE:?set KEMET_LE_STATE}"
cd "$KEMET_LE_REPO"
PY="${KEMET_LE_PYTHON:-python3}"
# 1. the configuration must be valid before anything else happens
"$PY" -m learning_engine --config "$KEMET_LE_CONFIG" --state "$KEMET_LE_STATE" validate-config > /dev/null
# 2. one run; the engine itself refuses to overlap (a lock in the state directory) and is idempotent per day
mkdir -p "$KEMET_LE_STATE"
chmod 700 "$KEMET_LE_STATE"
LOG="$KEMET_LE_STATE/run.log"
touch "$LOG" && chmod 600 "$LOG"
{
  echo "--- $(date '+%Y-%m-%d %H:%M:%S %Z') start"
  "$PY" -m learning_engine --config "$KEMET_LE_CONFIG" --state "$KEMET_LE_STATE" run | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print(r["date"], r["mode"], r["status"], "errors:", len(r["errors"]))'
  echo "--- $(date '+%Y-%m-%d %H:%M:%S %Z') end"
} >> "$LOG" 2>&1
