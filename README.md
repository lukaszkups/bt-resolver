# bt-resolver

Linux Bluetooth **usable-connection** checks and failure diagnostics for BlueZ.

This is **not** a replacement for Plasma / Blueman / GNOME Settings. Those GUIs already cover everyday scan/pair/connect. `bt-resolver` answers a different question: *is this link actually usable*, and *why not* when BlueZ says `Connected: yes` but the device flaps or never exposes HID/audio.

Typical failure it classifies (seen with Xbox Series + some Intel adapters):

- connect/disconnect flap (logo blink loop)
- empty GATT / no HID UUID after connect
- `br-connection-page-timeout`
- missing `xpadneo` / no `/dev/input` node
- stale bond / auth-on-reconnect

## Install

Requires Linux with BlueZ (`bluetoothd`) and system D-Bus access.

```bash
# recommended (CLI + tray GUI)
pipx install '.[tray]'

# CLI only
pipx install .

# or editable
python3 -m pip install -e ".[dev]"

# tray GUI (Plasma / StatusNotifier)
python3 -m pip install -e ".[tray]"
```

pipx installs every app into its own isolated virtualenv, so `pipx install PySide6`
does **not** make PySide6 visible to bt-resolver. If you already ran `pipx install .`,
add PySide6 to bt-resolver's venv instead:

```bash
pipx inject bt-resolver PySide6
```

## Tray GUI

```bash
bt-resolver-tray
# or
bt-resolver tray
```

Shows a tray icon:

- **Left-click** — panel that stays open until you click away (or click the icon again)
- **Right-click** — Plasma’s native StatusNotifier menu

The Gtk `appmenu-gtk-module` message is harmless.

### Autostart (login)

```bash
./scripts/install-autostart.sh
# remove later:
./scripts/uninstall-autostart.sh
# or: ./scripts/install-autostart.sh --remove
```

Writes `~/.config/autostart/bt-resolver-tray.desktop` with an absolute `Exec=` to your `.venv` (or `bt-resolver-tray` on `PATH`). Optional: `--exec /path/to/bt-resolver-tray`.

## CLI

```text
bt-resolver doctor
bt-resolver diagnose                 # host only
bt-resolver diagnose 44:16:22:DF:3D:22
bt-resolver scan --timeout 15
bt-resolver devices --paired
bt-resolver info Xbox
bt-resolver pair 44:16:22:DF:3D:22
bt-resolver connect Xbox --wait-stable 5
bt-resolver resolve Xbox --wake-scan 6   # low-level: LE wake + ConnectProfile(HID) + cycle
bt-resolver disconnect Xbox
bt-resolver remove Xbox --purge-cache
```

Exit code `0` on success; non-zero with `error[<reason>]: ...` on stderr.

## Library

```python
import asyncio
from bt_resolver import BluetoothManager

async def main():
    mgr = await BluetoothManager.connect_bus()
    try:
        result = await mgr.connect("Xbox Wireless Controller", wait_stable=5)
        print(result.usable, result.profile, result.messages)
    finally:
        await mgr.close()

asyncio.run(main())
```

## Kubuntu / Plasma notes

- If Plasma already registered a pairing agent, `bt-resolver` will **not** steal it. Pairing PIN UI stays with the desktop; connect/diagnose still work.
- Xbox Bluetooth usually needs [xpadneo](https://github.com/atar-axis/xpadneo).
- Firmware issues on the controller itself are outside this tool — update via Xbox Accessories on Windows when HID never starts.

## License

MIT
