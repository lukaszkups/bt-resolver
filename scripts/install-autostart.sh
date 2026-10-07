#!/usr/bin/env bash
# Install bt-resolver-tray into the current user's XDG autostart.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_DST="$AUTOSTART_DIR/bt-resolver-tray.desktop"
REMOVE=0

usage() {
  cat <<'EOF'
Usage: install-autostart.sh [--remove] [--exec PATH]

  Installs (or removes) bt-resolver-tray from user autostart
  (~/.config/autostart/bt-resolver-tray.desktop).

Options:
  --remove       Remove autostart entry
  --exec PATH    Explicit path to bt-resolver-tray (default: auto-detect)
  -h, --help     Show this help
EOF
}

TRAY_EXEC=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remove) REMOVE=1; shift ;;
    --exec)
      TRAY_EXEC="${2:-}"
      if [[ -z "$TRAY_EXEC" ]]; then
        echo "error: --exec requires a path" >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$REMOVE" -eq 1 ]]; then
  if [[ -f "$DESKTOP_DST" ]]; then
    rm -f "$DESKTOP_DST"
    echo "Removed autostart: $DESKTOP_DST"
  else
    echo "No autostart entry at $DESKTOP_DST"
  fi
  exit 0
fi

resolve_tray() {
  if [[ -n "$TRAY_EXEC" ]]; then
    if [[ -x "$TRAY_EXEC" ]]; then
      realpath "$TRAY_EXEC"
      return 0
    fi
    echo "error: not executable: $TRAY_EXEC" >&2
    return 1
  fi

  local candidates=(
    "$ROOT/.venv/bin/bt-resolver-tray"
    "$ROOT/venv/bin/bt-resolver-tray"
  )
  if command -v bt-resolver-tray >/dev/null 2>&1; then
    candidates+=("$(command -v bt-resolver-tray)")
  fi

  local c
  for c in "${candidates[@]}"; do
    if [[ -x "$c" ]]; then
      realpath "$c"
      return 0
    fi
  done

  echo "error: could not find bt-resolver-tray" >&2
  echo "Install the tray extra first, e.g.:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[tray]'" >&2
  echo "Or pass: $0 --exec /path/to/bt-resolver-tray" >&2
  return 1
}

TRAY_EXEC="$(resolve_tray)"
mkdir -p "$AUTOSTART_DIR"

cat >"$DESKTOP_DST" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=bt-resolver
Comment=Bluetooth usable-connection tray for BlueZ
Exec=${TRAY_EXEC}
Icon=bluetooth
Terminal=false
Categories=Utility;System;
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
EOF

chmod 644 "$DESKTOP_DST"
echo "Autostart installed:"
echo "  $DESKTOP_DST"
echo "  Exec=$TRAY_EXEC"
echo
echo "It will start on next login. To start now:"
echo "  \"$TRAY_EXEC\" &"
echo
echo "To remove later:"
echo "  $0 --remove"
