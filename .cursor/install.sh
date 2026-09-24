#!/usr/bin/env bash
# Idempotent environment bootstrap for the 买家秀生成 Agent (FastAPI + LangGraph).
set -euo pipefail

cd "$(dirname "$0")/.."

# System package needed to create Python virtual environments on the default image.
if ! dpkg -s python3-venv >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv python3-pip
fi

# Create the virtual environment once; reuse it on subsequent installs.
if [ ! -x "venv/bin/python" ]; then
  python3 -m venv venv
fi

venv/bin/python -m pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt

# Ensure runtime data/log directories exist (they are gitignored).
mkdir -p data/generated_images data/outbox_files logs

echo "Environment setup complete."
