#!/usr/bin/env bash
set -euo pipefail
cd /home/lotus/chaoxing-evolved
umask 077
exec .venv/bin/python -u -m web_dashboard.server --host 10.10.10.18 --port 8765 --refresh-seconds 180
