"""Low-level multi-strategy Bluetooth connection resolution."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from bt_resolver.device import UUID_A2DP_SINK, UUID_HID, UUID_HID_BREDR, DeviceInfo
import asyncio

from bt_resolver.manager import BluetoothManager, StabilitySample
from bt_resolver.profiles import detect_profile, verify_usable_connection


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str


@dataclass
class ResolveResult:
    device: DeviceInfo
    profile: str
    usable: bool
    stable: bool
    stability: StabilitySample | None
    steps: list[StepResult] = field(default_factory=list)
    reason: str = "ok"
    winning_strategy: str = ""

    def format(self) -> str:
        lines = [
            f"resolve: {self.device.display_name} ({self.device.address})",
            f"profile={self.profile} usable={self.usable} stable={self.stable} "
            f"strategy={self.winning_strategy or '-'} reason={self.reason}",
            "steps:",
        ]
        for s in self.steps:
            mark = "OK" if s.ok else "FAIL"
            lines.append(f"  [{mark}] {s.name}: {s.detail}")
        return "\n".join(lines) + "\n"


def _profile_uuids(device: DeviceInfo, profile_name: str) -> list[str]:
    uuids: list[str] = []
    if profile_name == "dualshock" or device.has_uuid(UUID_HID_BREDR):
        uuids.append(UUID_HID_BREDR)
    if profile_name == "xbox" or device.has_uuid(UUID_HID):
        uuids.append(UUID_HID)
    if profile_name == "audio" or device.has_uuid(UUID_A2DP_SINK):
        uuids.append(UUID_A2DP_SINK)
    seen: set[str] = set()
    out: list[str] = []
    for u in uuids:
        key = u.lower()
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out


async def resolve_connection(
    manager: BluetoothManager,
    query: str,
    *,
    wait_stable: float = 4.0,
    wake_scan: float = 6.0,
    usable_wait: float = 8.0,
    ) -> ResolveResult:
    """
    Escalate through low-level strategies until the link is usable.

    Strategies:
      1. prepare — power, trust, WakeAllowed
      2. le_wake_scan — brief LE discovery so the peripheral advertises/RSSI updates
      3. connect — Device1.Connect
      4. wait_services — ServicesResolved
      5. connect_profile — Device1.ConnectProfile(HID/A2DP)
      6. cycle_reconnect — disconnect + wake + connect + profile
      7. wait_usable — poll profile endpoints (/dev/input, audio sink)
    """
    await manager.ensure_agent()
    await manager.set_powered(True)
    device = await manager.get_device(query)
    profile = detect_profile(device)
    steps: list[StepResult] = []
    winning = ""
    stability: StabilitySample | None = None
    reason = "not_connected"

    try:
        if not device.trusted:
            device = await manager.trust(device.address)
            steps.append(StepResult("prepare.trust", True, "Trusted=true"))
        await manager.set_wake_allowed(device.path, True)
        steps.append(StepResult("prepare", True, "Powered + WakeAllowed"))
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult("prepare", False, str(exc)))

    async def _evaluate(label: str) -> bool:
        nonlocal device, stability, reason
        stability = await manager.sample_connected(device.path, duration=max(0.5, wait_stable))
        device = await manager.refresh_device(device.path)
        if stability.flap:
            reason = "connect_flap"
            steps.append(
                StepResult(
                    f"verify:{label}",
                    False,
                    f"flap ratio={stability.connected_ratio:.0%} "
                    f"samples={''.join('Y' if s else 'n' for s in stability.samples)}",
                )
            )
            return False
        if not stability.final_connected:
            reason = "not_connected"
            steps.append(StepResult(f"verify:{label}", False, "not connected after attempt"))
            return False

        device = await manager.wait_services_resolved(device.path, timeout=min(5.0, usable_wait))
        ok = False
        detail = ""
        fail_reason = "not_usable"
        deadline = time.monotonic() + usable_wait
        while time.monotonic() < deadline:
            device = await manager.refresh_device(device.path)
            ok, detail, fail_reason = verify_usable_connection(device)
            if ok:
                break
            await asyncio.sleep(0.4)
        reason = "ok" if ok else fail_reason
        steps.append(
            StepResult(
                f"verify:{label}",
                ok,
                f"connected={device.connected} services={device.services_resolved} "
                f"stable_ratio={stability.connected_ratio:.0%} {detail}",
            )
        )
        return ok

    try:
        seen = await manager.wake_scan_for(device.address, timeout=wake_scan)
        steps.append(
            StepResult(
                "le_wake_scan",
                True,
                f"discovery {wake_scan:.0f}s; advertising_seen={seen}",
            )
        )
        if not seen:
            steps.append(
                StepResult(
                    "le_wake_scan.hint",
                    False,
                    "No RSSI during scan — wake the device (Xbox logo) if offline",
                )
            )
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult("le_wake_scan", False, str(exc)))

    device = await manager.refresh_device(device.path)

    try:
        await manager.connect_raw(device.path, timeout=18.0)
        steps.append(StepResult("connect", True, "Device1.Connect ok"))
        winning = "connect"
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult("connect", False, str(exc)))
        try:
            note = await manager.abort_connection_attempt(device.path)
            steps.append(StepResult("abort_busy", True, note))
        except Exception as abort_exc:  # noqa: BLE001
            steps.append(StepResult("abort_busy", False, str(abort_exc)))

    if await _evaluate("after_connect"):
        return ResolveResult(
            device=device,
            profile=profile.name,
            usable=True,
            stable=True,
            stability=stability,
            steps=steps,
            reason="ok",
            winning_strategy=winning or "connect",
        )

    try:
        device = await manager.wait_services_resolved(device.path, timeout=5.0)
        steps.append(
            StepResult(
                "wait_services",
                device.services_resolved,
                f"ServicesResolved={device.services_resolved}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult("wait_services", False, str(exc)))

    # Prefer ConnectProfile only once ACL/LE is up; otherwise abort first
    device = await manager.refresh_device(device.path)
    if not device.connected:
        try:
            note = await manager.abort_connection_attempt(device.path)
            steps.append(StepResult("pre_profile_abort", True, note))
        except Exception as exc:  # noqa: BLE001
            steps.append(StepResult("pre_profile_abort", False, str(exc)))
        # Second short connect before profile attach
        try:
            await manager.wake_scan_for(device.address, timeout=min(3.0, wake_scan))
            await manager.connect_raw(device.path, timeout=12.0)
            steps.append(StepResult("connect_retry", True, "Device1.Connect retry"))
            winning = "connect_retry"
        except Exception as exc:  # noqa: BLE001
            steps.append(StepResult("connect_retry", False, str(exc)))

    for uuid in _profile_uuids(device, profile.name):
        try:
            await manager.connect_profile(device.path, uuid, timeout=15.0)
            steps.append(StepResult("connect_profile", True, f"ConnectProfile({uuid})"))
            winning = f"connect_profile:{uuid}"
            if await _evaluate("after_profile"):
                return ResolveResult(
                    device=device,
                    profile=profile.name,
                    usable=True,
                    stable=True,
                    stability=stability,
                    steps=steps,
                    reason="ok",
                    winning_strategy=winning,
                )
        except Exception as exc:  # noqa: BLE001
            steps.append(StepResult("connect_profile", False, f"{uuid}: {exc}"))
            try:
                note = await manager.abort_connection_attempt(device.path)
                steps.append(StepResult("profile_abort", True, note))
            except Exception:  # noqa: BLE001
                pass

    try:
        device = await manager.refresh_device(device.path)
        try:
            note = await manager.abort_connection_attempt(device.path)
            steps.append(StepResult("cycle.abort", True, note))
        except Exception as exc:  # noqa: BLE001
            steps.append(StepResult("cycle.abort", False, str(exc)))
        await asyncio.sleep(0.8)

        await manager.wake_scan_for(device.address, timeout=min(4.0, wake_scan))
        await manager.connect_raw(device.path, timeout=18.0)
        steps.append(StepResult("cycle.connect", True, "Reconnect after cycle"))
        for uuid in _profile_uuids(device, profile.name):
            try:
                await manager.connect_profile(device.path, uuid, timeout=12.0)
                steps.append(StepResult("cycle.profile", True, uuid))
                winning = f"cycle:{uuid}"
                break
            except Exception as exc:  # noqa: BLE001
                steps.append(StepResult("cycle.profile", False, f"{uuid}: {exc}"))

        if await _evaluate("after_cycle"):
            return ResolveResult(
                device=device,
                profile=profile.name,
                usable=True,
                stable=True,
                stability=stability,
                steps=steps,
                reason="ok",
                winning_strategy=winning or "cycle",
            )
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult("cycle", False, str(exc)))
        device = await manager.refresh_device(device.path)

    return ResolveResult(
        device=device,
        profile=profile.name,
        usable=False,
        stable=bool(stability and not stability.flap and stability.final_connected),
        stability=stability,
        steps=steps,
        reason=reason or "connect_failed",
        winning_strategy=winning,
    )
