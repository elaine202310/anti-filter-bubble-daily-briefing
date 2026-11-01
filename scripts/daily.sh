#!/bin/sh
# Run with cron/launchd from the project directory; credentials are loaded from .env.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONPATH="$PWD/src"
python3 -m briefing_pipeline.cli run
if [ "${BRIEFING_SEND_EMAIL:-false}" = true ]; then
    python3 -m briefing_pipeline.cli send-subscribed
fi
