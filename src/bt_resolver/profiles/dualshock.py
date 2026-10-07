"""Sony DualShock / DualSense (PS4/PS5) Bluetooth gamepad profile."""

from __future__ import annotations

import os
import re
from pathlib import Path

from bt_resolver.device import UUID_HID_BREDR, DeviceInfo
from bt_resolver.profiles.base import Profile

SONY_VENDOR = "054c"
# DualShock 4 (CUH-ZCT1/ZCT2), DualSense, etc.
SONY_PRODUCTS = {"05c4", "09cc", "0ce6", "0df2"}
NAME_RE = re.compile(r"wireless controller|dualshock|dualsense|ps[45]|sony.*controller", re.I)


class DualShockProfile(Profile):
    name = "dualshock"

    def matches(self, device: DeviceInfo) -> bool:
        mod = (device.modalias or "").lower()
        m = re.search(r"v([0-9a-f]{4})p([0-9a-f]{4})", mod)
        if m and m.group(1) == SONY_VENDOR and m.group(2) in SONY_PRODUCTS:
            return True
        if m and m.group(1) == SONY_VENDOR and device.icon == "input-gaming":
            return True
        if NAME_RE.search(device.display_name) or NAME_RE.search(device.name):
            if device.icon == "input-gaming" or device.has_uuid(UUID_HID_BREDR):
                return True
        # Exact DS4 default name + HID
        if device.name == "Wireless Controller" and (
            device.has_uuid(UUID_HID_BREDR) or device.icon == "input-gaming"
        ):
            return True
        return False

    def verify(self, device: DeviceInfo) -> tuple[bool, str, str]:
        if not device.connected:
            return False, "PlayStation controller not connected", "not_connected"

        has_hid = device.has_uuid(UUID_HID_BREDR)
        nodes = find_sony_input_nodes()
        driver = find_sony_hid_driver()

        if nodes:
            drv = driver or "hid-sony/hid-playstation"
            return True, f"Usable gamepad via {drv}; input: {', '.join(nodes[:4])}", "ok"

        if not has_hid and not nodes:
            return (
                False,
                "Connected but no classic HID UUID (00001124) and no input nodes — "
                "pair in PS4 pairing mode (Share+PS) or check hid-sony / hid-playstation",
                "no_hid",
            )

        return (
            False,
            "HID present but no /dev/input nodes yet — ensure kernel modules "
            "hid-sony or hid-playstation are available",
            "no_input_device",
        )


def find_sony_input_nodes() -> list[str]:
    found: list[str] = []
    by_id = Path("/dev/input/by-id")
    if by_id.is_dir():
        for p in sorted(by_id.iterdir()):
            n = p.name.lower()
            if any(k in n for k in ("sony", "dualshock", "dualsense", "ps4", "ps5", "wireless-controller")):
                found.append(str(p))
    try:
        text = Path("/proc/bus/input/devices").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if re.search(r"Sony|DualShock|DualSense|Wireless Controller", text, re.I):
        for block in text.split("\n\n"):
            if re.search(r"Sony|DualShock|DualSense|Wireless Controller", block, re.I):
                m = re.search(r"Handlers=(.+)", block)
                if m:
                    for tok in m.group(1).split():
                        if tok.startswith(("js", "event")):
                            path = f"/dev/input/{tok}"
                            if path not in found:
                                found.append(path)
    return found


def find_sony_hid_driver() -> str | None:
    hid = Path("/sys/bus/hid/devices")
    if not hid.is_dir():
        return None
    for device in hid.iterdir():
        name = device.name.upper()
        if "054C" not in name and "054C" not in name.replace(":", ""):
            try:
                content = (device / "uevent").read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if SONY_VENDOR not in content and "sony" not in content:
                continue
        driver_link = device / "driver"
        if driver_link.is_symlink():
            return os.path.basename(os.readlink(driver_link))
    return None
