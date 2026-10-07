from __future__ import annotations

import os
import re
from pathlib import Path

from bt_resolver.device import UUID_HID, DeviceInfo
from bt_resolver.profiles.base import Profile

XBOX_NAME_RE = re.compile(r"xbox", re.I)
MS_VENDOR = "045e"


class XboxProfile(Profile):
    name = "xbox"

    def matches(self, device: DeviceInfo) -> bool:
        if XBOX_NAME_RE.search(device.display_name) or XBOX_NAME_RE.search(device.name):
            return True
        if device.icon == "input-gaming" and device.has_uuid(UUID_HID):
            # Gaming HID — treat as xbox-like if Microsoft modalias
            if MS_VENDOR in (device.modalias or "").lower():
                return True
        if MS_VENDOR in (device.modalias or "").lower() and "input-gaming" in device.icon:
            return True
        return False

    def verify(self, device: DeviceInfo) -> tuple[bool, str, str]:
        if not device.connected:
            return False, "Xbox controller not connected", "not_connected"

        has_hid = device.has_uuid(UUID_HID)
        input_nodes = find_xbox_input_nodes()
        driver = find_xbox_hid_driver()
        xpadneo_loaded = module_loaded("hid_xpadneo") or module_loaded("hid-xpadneo")

        if input_nodes and (driver or xpadneo_loaded or has_hid):
            nodes = ", ".join(input_nodes[:4])
            drv = driver or ("hid-xpadneo" if xpadneo_loaded else "unknown")
            return True, f"Usable gamepad via {drv}; input: {nodes}", "ok"

        if not has_hid and not input_nodes:
            return (
                False,
                "Connected but no HID UUID and no /dev/input nodes — empty GATT / HID-over-GATT failure "
                "(common on some Intel BT + Xbox Series combos). Try firmware update, xpadneo, or another adapter.",
                "no_hid",
            )

        if has_hid and not input_nodes:
            hint = ""
            if not xpadneo_loaded:
                hint = " Load xpadneo (`sudo modprobe hid-xpadneo`) or install https://github.com/atar-axis/xpadneo."
            return (
                False,
                f"HID UUID present but no input device nodes yet.{hint}",
                "no_input_device",
            )

        if input_nodes and not xpadneo_loaded and not driver:
            return (
                True,
                f"Input nodes present ({', '.join(input_nodes[:3])}) but driver unclear; check lsmod for hid-xpadneo",
                "ok",
            )

        return False, "Connected but gamepad stack incomplete", "not_usable"


def module_loaded(name: str) -> bool:
    name = name.replace("-", "_")
    try:
        text = Path("/proc/modules").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.split()[0].replace("-", "_") == name for line in text.splitlines() if line)


def find_xbox_input_nodes() -> list[str]:
    found: list[str] = []
    by_id = Path("/dev/input/by-id")
    if by_id.is_dir():
        for p in sorted(by_id.iterdir()):
            if "xbox" in p.name.lower() or "x-box" in p.name.lower():
                found.append(str(p))
    # /proc/bus/input/devices handlers
    try:
        text = Path("/proc/bus/input/devices").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if re.search(r"Xbox|Microsoft.*Controller", text, re.I):
        for block in text.split("\n\n"):
            if re.search(r"Xbox|Microsoft.*Controller", block, re.I):
                m = re.search(r"Handlers=(.+)", block)
                if m:
                    for tok in m.group(1).split():
                        if tok.startswith(("js", "event")):
                            path = f"/dev/input/{tok}"
                            if path not in found:
                                found.append(path)
    # fallback js*
    if not found:
        for p in sorted(Path("/dev/input").glob("js*")):
            found.append(str(p))
    return found


def find_xbox_hid_driver() -> str | None:
    hid = Path("/sys/bus/hid/devices")
    if not hid.is_dir():
        return None
    for device in hid.iterdir():
        # 0005:045E:.... Bluetooth HID
        if MS_VENDOR.upper() not in device.name.upper() and MS_VENDOR not in device.name.lower():
            # also check uevent
            uevent = device / "uevent"
            try:
                content = uevent.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if MS_VENDOR not in content and "xbox" not in content:
                continue
        driver_link = device / "driver"
        if driver_link.is_symlink():
            return os.path.basename(os.readlink(driver_link))
        # module name from modalias
        try:
            modalias = (device / "modalias").read_text(encoding="utf-8").strip()
            if MS_VENDOR in modalias.lower() or "045E" in modalias.upper():
                return "hid"
        except OSError:
            pass
    return None
