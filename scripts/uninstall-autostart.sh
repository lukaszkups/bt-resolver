#!/usr/bin/env bash
# Remove bt-resolver-tray from user autostart.
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install-autostart.sh" --remove
