#!/usr/bin/env bash
# Install on Raspberry Pi OS Lite 32-bit (Bookworm), Pi Zero W
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/renogy-monitor}"

echo "==> BlueZ LE-only basics"
sudo btmgmt le on || true
sudo btmgmt bredr off || true
sudo hciconfig hci0 noscan || true

echo "==> Install package to ${PREFIX}"
mkdir -p "$PREFIX"
sudo install -d -o pi -g pi /var/lib/renogy-monitor
rsync -a --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  "$ROOT/" "$PREFIX/"

cd "$PREFIX"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -e .

if [[ ! -f "$PREFIX/config.yaml" ]]; then
  cp "$PREFIX/deploy/config.example.yaml" "$PREFIX/config.yaml"
  echo "Created config.yaml — edit MACs after: renogyctl scan"
fi

echo "==> systemd unit (optional)"
if [[ "${INSTALL_SYSTEMD:-0}" == "1" ]]; then
  sudo cp "$PREFIX/deploy/renogy-monitor.service" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable renogy-monitor.service
  echo "Enabled renogy-monitor.service (start after Phase 2)"
fi

echo "Done. Try: renogyctl scan"
