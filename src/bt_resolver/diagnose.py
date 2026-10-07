"""Host doctor and per-device diagnostics."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bt_resolver.device import UUID_HID, UUID_HID_BREDR
from bt_resolver.manager import BluetoothManager, StabilitySample
from bt_resolver.profiles import detect_profile, verify_usable_connection
from bt_resolver.profiles.dualshock import find_sony_hid_driver, find_sony_input_nodes
from bt_resolver.profiles.xbox import (
    find_xbox_hid_driver,
    find_xbox_input_nodes,
    module_loaded,
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    severity: str = "info"  # info | warn | error


@dataclass
class Report:
    title: str
    checks: list[Check] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(c.severity == "error" and not c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str, severity: str | None = None) -> None:
        if severity is None:
            severity = "info" if ok else "error"
        self.checks.append(Check(name=name, ok=ok, detail=detail, severity=severity))

    def format(self) -> str:
        lines = [self.title, "=" * len(self.title)]
        for c in self.checks:
            mark = "OK" if c.ok else c.severity.upper()
            lines.append(f"[{mark}] {c.name}: {c.detail}")
        if self.hints:
            lines.append("")
            lines.append("Hints:")
            for h in self.hints:
                lines.append(f"  - {h}")
        return "\n".join(lines) + "\n"


def _bluez_version() -> str:
    for cmd in (["bluetoothd", "-v"], ["bluetoothctl", "--version"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=3)
            return out.strip().splitlines()[0]
        except (subprocess.SubprocessError, OSError):
            continue
    return "unknown"


def _service_active(name: str) -> bool | None:
    if not shutil.which("systemctl"):
        return None
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            timeout=3,
            check=False,
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return None


async def run_doctor(manager: BluetoothManager) -> Report:
    report = Report(title="bt-resolver doctor")
    report.meta["bluez"] = _bluez_version()
    report.add("bluez_version", True, report.meta["bluez"], severity="info")

    bt_active = _service_active("bluetooth")
    if bt_active is None:
        report.add("bluetooth_service", True, "systemctl unavailable; skipped", severity="warn")
    else:
        report.add(
            "bluetooth_service",
            bt_active,
            "active" if bt_active else "not active — try: sudo systemctl start bluetooth",
            severity="error" if not bt_active else "info",
        )

    try:
        adapters = await manager.list_adapters()
    except Exception as exc:  # noqa: BLE001
        report.add("adapters", False, str(exc), severity="error")
        report.hints.append("Ensure bluez is installed and bluetoothd is running.")
        return report

    if not adapters:
        report.add("adapters", False, "No adapters found", severity="error")
        report.hints.append("Check rfkill / BIOS Bluetooth / USB dongle.")
        return report

    for a in adapters:
        report.add(
            f"adapter:{a.address}",
            a.powered,
            f"{a.name or 'adapter'} powered={a.powered} discovering={a.discovering}",
            severity="error" if not a.powered else "info",
        )
        if not a.powered:
            report.hints.append("Power on with: bt-resolver adapters (auto-powers on connect) or bluetoothctl power on")

    agent_note = await manager.ensure_agent()
    report.add(
        "pairing_agent",
        True,
        agent_note,
        severity="warn" if "Could not" in agent_note else "info",
    )
    if "already registered" in agent_note.lower():
        report.hints.append(
            "Desktop agent (Plasma/GNOME) owns pairing UI — prefer pairing there, or use bt-resolver pair while device is in pairing mode."
        )

    xpadneo = module_loaded("hid_xpadneo")
    report.add(
        "xpadneo",
        True,
        "loaded" if xpadneo else "not loaded (needed for Xbox Bluetooth)",
        severity="info" if xpadneo else "warn",
    )
    if not xpadneo:
        report.hints.append("For Xbox pads: install xpadneo and `sudo modprobe hid-xpadneo`.")

    main_conf = Path("/etc/bluetooth/main.conf")
    if main_conf.is_file():
        text = main_conf.read_text(encoding="utf-8", errors="replace")
        has_dual = "ControllerMode" in text
        report.add("main.conf", True, f"present; ControllerMode mentioned={has_dual}", severity="info")
    else:
        report.add("main.conf", True, "not found (defaults apply)", severity="info")

    return report


async def run_diagnose(
    manager: BluetoothManager,
    query: str | None,
    *,
    sample_seconds: float = 5.0,
) -> Report:
    if not query:
        return await run_doctor(manager)

    report = Report(title=f"bt-resolver diagnose: {query}")
    doctor = await run_doctor(manager)
    report.checks.extend(doctor.checks)
    report.hints.extend(doctor.hints)

    try:
        device = await manager.get_device(query)
    except Exception as exc:  # noqa: BLE001
        report.add("device", False, str(exc), severity="error")
        report.hints.append("Run: bt-resolver scan  then retry with MAC or name")
        return report

    profile = detect_profile(device)
    report.meta["device"] = device
    report.meta["profile"] = profile.name
    report.add(
        "device",
        True,
        f"{device.display_name} [{device.address}] profile={profile.name} "
        f"paired={device.paired} bonded={device.bonded} trusted={device.trusted} connected={device.connected}",
        severity="info",
    )
    report.add(
        "uuids",
        True,
        f"{len(device.uuids)} UUIDs; HID={device.has_uuid(UUID_HID)}",
        severity="info",
    )
    if device.modalias:
        report.add("modalias", True, device.modalias, severity="info")

    if not device.connected:
        report.add(
            "connected",
            False,
            "Device is not connected — wake it and run: bt-resolver connect <name|MAC>",
            severity="warn",
        )
        report.hints.append("Diagnose does not auto-connect (BlueZ Connect can block for minutes).")
    else:
        stability = await manager.sample_connected(device.path, duration=sample_seconds)
        device = await manager.refresh_device(device.path)
        report.add(
            "stability",
            not stability.flap and stability.final_connected,
            f"final_connected={stability.final_connected} flap={stability.flap} "
            f"ratio={stability.connected_ratio:.0%} over {sample_seconds}s "
            f"samples={''.join('Y' if s else 'n' for s in stability.samples)}",
            severity="error" if stability.flap or not stability.final_connected else "info",
        )
        if stability.flap:
            report.hints.append(
                "Connect flap usually means HID/auth failure (Xbox logo blink). "
                "Try firmware update, remove+re-pair in pairing mode, or a non-Intel BT dongle."
            )

    ok, detail, fail_reason = verify_usable_connection(device)
    report.add(
        "usable",
        ok,
        f"{detail} (reason={fail_reason})",
        severity="info" if ok else "error",
    )

    if profile.name == "xbox":
        nodes = find_xbox_input_nodes()
        driver = find_xbox_hid_driver()
        report.add(
            "xbox_input",
            bool(nodes),
            f"nodes={nodes or 'none'} driver={driver or 'none'} xpadneo={module_loaded('hid_xpadneo')}",
            severity="error" if not nodes and device.connected else "info",
        )
        if device.connected and not device.has_uuid(UUID_HID) and not nodes:
            report.hints.append(
                "Empty HID/GATT while Connected=yes — classic BlueZ + Xbox BLE failure. "
                "Update controller firmware (Xbox Accessories on Windows) or use USB/other adapter."
            )
        if not device.paired or not device.bonded:
            report.hints.append("Device not bonded — put pad in pairing mode and run: bt-resolver pair <MAC>")
        if device.paired and not ok:
            report.hints.append(
                "Stale bonds can cause auth loops. Try: bt-resolver remove <MAC> --purge-cache "
                "then re-pair with the pad in pairing mode."
            )

    if profile.name == "dualshock":
        nodes = find_sony_input_nodes()
        driver = find_sony_hid_driver()
        report.add(
            "dualshock_input",
            bool(nodes),
            f"nodes={nodes or 'none'} driver={driver or 'none'} "
            f"hid_bredr={device.has_uuid(UUID_HID_BREDR)}",
            severity="error" if not nodes and device.connected else "info",
        )
        if not device.paired or not device.bonded:
            report.hints.append(
                "PS4 DualShock: hold Share+PS until the light bar blinks quickly, then: "
                "bt-resolver pair 'Wireless Controller'   (or use tray → Pair + trust)"
            )
        if device.paired and not ok:
            report.hints.append(
                "If connect flaps: remove bond and re-pair in Share+PS mode; "
                "ensure hid-sony / hid-playstation is available in the kernel."
            )

    # Deduplicate hints
    seen: set[str] = set()
    unique_hints: list[str] = []
    for h in report.hints:
        if h not in seen:
            seen.add(h)
            unique_hints.append(h)
    report.hints = unique_hints
    return report
