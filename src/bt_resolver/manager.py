"""BlueZ D-Bus manager: adapters, scan, pair/trust/connect/remove."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from bt_resolver.agent import (
    parse_bluez_error,
    try_register_agent,
    unregister_agent,
)
from bt_resolver.device import DeviceInfo, _unwrap, normalize_mac, resolve_device
from bt_resolver.errors import (
    AdapterError,
    ConnectError,
    ConnectFlapError,
    PairError,
    UsableConnectionError,
)

LOG = logging.getLogger(__name__)

BLUEZ = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
ADAPTER_IFACE = "org.bluez.Adapter1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"


@dataclass
class AdapterInfo:
    path: str
    address: str
    name: str
    powered: bool
    discoverable: bool
    pairable: bool
    discovering: bool


@dataclass
class StabilitySample:
    samples: list[bool]
    flap: bool
    final_connected: bool

    @property
    def connected_ratio(self) -> float:
        if not self.samples:
            return 0.0
        return sum(1 for s in self.samples if s) / len(self.samples)


@dataclass
class ConnectResult:
    device: DeviceInfo
    stable: bool
    stability: StabilitySample
    usable: bool
    profile: str
    messages: list[str]
    reason: str = "ok"


class BluetoothManager:
    """Async BlueZ client."""

    def __init__(self, bus: MessageBus) -> None:
        self.bus = bus
        self._agent_registered = False
        self._agent_note = ""

    @classmethod
    async def connect_bus(cls) -> BluetoothManager:
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(
                f"Cannot connect to system D-Bus (is bluetoothd running?): {exc}",
                reason="dbus_unavailable",
            ) from exc
        return cls(bus)

    async def close(self) -> None:
        if self._agent_registered:
            await unregister_agent(self.bus)
            self._agent_registered = False
        self.bus.disconnect()

    async def ensure_agent(self) -> str:
        if self._agent_registered:
            return self._agent_note
        registered, note = await try_register_agent(self.bus)
        self._agent_registered = registered
        self._agent_note = note
        return note

    async def _managed_objects(self) -> dict[str, dict[str, dict[str, Any]]]:
        intro = await self.bus.introspect(BLUEZ, "/")
        root = self.bus.get_proxy_object(BLUEZ, "/", intro)
        om = root.get_interface(OBJECT_MANAGER)
        return await om.call_get_managed_objects()

    async def list_adapters(self) -> list[AdapterInfo]:
        objects = await self._managed_objects()
        adapters: list[AdapterInfo] = []
        for path, ifaces in objects.items():
            props = {k: _unwrap(v) for k, v in (ifaces.get(ADAPTER_IFACE) or {}).items()}
            if not props:
                continue
            adapters.append(
                AdapterInfo(
                    path=path,
                    address=str(props.get("Address", "")),
                    name=str(props.get("Alias") or props.get("Name") or ""),
                    powered=bool(props.get("Powered", False)),
                    discoverable=bool(props.get("Discoverable", False)),
                    pairable=bool(props.get("Pairable", False)),
                    discovering=bool(props.get("Discovering", False)),
                )
            )
        return adapters

    async def default_adapter(self) -> AdapterInfo:
        adapters = await self.list_adapters()
        if not adapters:
            raise AdapterError("No Bluetooth adapter found", reason="no_adapter")
        powered = [a for a in adapters if a.powered]
        return powered[0] if powered else adapters[0]

    async def _adapter_iface(self, adapter_path: str | None = None):
        adapter = await self.default_adapter() if adapter_path is None else None
        path = adapter_path or adapter.path
        intro = await self.bus.introspect(BLUEZ, path)
        obj = self.bus.get_proxy_object(BLUEZ, path, intro)
        return obj.get_interface(ADAPTER_IFACE), obj.get_interface(PROPS_IFACE), path

    async def set_powered(self, powered: bool = True, adapter_path: str | None = None) -> None:
        _, props, _ = await self._adapter_iface(adapter_path)
        await props.call_set(ADAPTER_IFACE, "Powered", Variant("b", powered))

    async def list_devices(self) -> list[DeviceInfo]:
        objects = await self._managed_objects()
        devices: list[DeviceInfo] = []
        for path, ifaces in objects.items():
            props = ifaces.get(DEVICE_IFACE)
            if not props:
                continue
            devices.append(DeviceInfo.from_props(path, props))
        devices.sort(key=lambda d: (not d.connected, not d.paired, d.display_name.lower()))
        return devices

    async def get_device(self, query: str) -> DeviceInfo:
        return resolve_device(await self.list_devices(), query)

    async def refresh_device(self, path: str) -> DeviceInfo:
        intro = await self.bus.introspect(BLUEZ, path)
        obj = self.bus.get_proxy_object(BLUEZ, path, intro)
        props_iface = obj.get_interface(PROPS_IFACE)
        props = await props_iface.call_get_all(DEVICE_IFACE)
        # dbus-next may return Variant values
        plain = {k: (v.value if hasattr(v, "value") else v) for k, v in props.items()}
        return DeviceInfo.from_props(path, plain)

    async def scan(self, timeout: float = 15.0, adapter_path: str | None = None) -> list[DeviceInfo]:
        await self.set_powered(True, adapter_path)
        adapter_iface, props, path = await self._adapter_iface(adapter_path)
        discovering = await props.call_get(ADAPTER_IFACE, "Discovering")
        discovering = discovering.value if hasattr(discovering, "value") else discovering
        started = False
        if not discovering:
            try:
                await adapter_iface.call_start_discovery()
                started = True
            except Exception as exc:  # noqa: BLE001
                reason, msg = parse_bluez_error(exc)
                raise AdapterError(f"Start discovery failed: {msg}", reason=reason) from exc
        try:
            await asyncio.sleep(timeout)
        finally:
            if started:
                try:
                    await adapter_iface.call_stop_discovery()
                except Exception:  # noqa: BLE001
                    pass
        return await self.list_devices()

    async def _device_iface(self, path: str):
        intro = await self.bus.introspect(BLUEZ, path)
        obj = self.bus.get_proxy_object(BLUEZ, path, intro)
        return obj.get_interface(DEVICE_IFACE), obj.get_interface(PROPS_IFACE)

    async def trust(self, query: str) -> DeviceInfo:
        device = await self.get_device(query)
        _, props = await self._device_iface(device.path)
        await props.call_set(DEVICE_IFACE, "Trusted", Variant("b", True))
        return await self.refresh_device(device.path)

    async def pair(self, query: str, timeout: float = 60.0) -> DeviceInfo:
        await self.ensure_agent()
        await self.set_powered(True)
        device = await self.get_device(query)
        if device.paired:
            return device
        iface, _ = await self._device_iface(device.path)
        try:
            await asyncio.wait_for(iface.call_pair(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise PairError(f"Pairing timed out after {timeout}s", reason="pair_timeout") from exc
        except Exception as exc:  # noqa: BLE001
            reason, msg = parse_bluez_error(exc)
            raise PairError(f"Pair failed: {msg}", reason=reason) from exc
        await self.trust(device.address)
        return await self.refresh_device(device.path)

    async def disconnect(self, query: str) -> DeviceInfo:
        device = await self.get_device(query)
        if not device.connected:
            return device
        iface, _ = await self._device_iface(device.path)
        try:
            await iface.call_disconnect()
        except Exception as exc:  # noqa: BLE001
            reason, msg = parse_bluez_error(exc)
            raise ConnectError(f"Disconnect failed: {msg}", reason=reason) from exc
        await asyncio.sleep(0.3)
        return await self.refresh_device(device.path)

    async def remove(self, query: str, *, purge_cache: bool = False) -> str:
        device = await self.get_device(query)
        adapter_path = device.adapter_path or (await self.default_adapter()).path
        address = device.address
        adapter_iface, _, _ = await self._adapter_iface(adapter_path)
        try:
            await adapter_iface.call_remove_device(device.path)
        except Exception as exc:  # noqa: BLE001
            reason, msg = parse_bluez_error(exc)
            raise ConnectError(f"Remove failed: {msg}", reason=reason) from exc

        note = f"Removed {address}"
        if purge_cache:
            note += self._purge_cache_hint(adapter_path, address)
        return note

    def _purge_cache_hint(self, adapter_path: str, address: str) -> str:
        # Adapter MAC from path is not always available; use filesystem hint.
        mac = normalize_mac(address)
        return (
            f". To purge leftover keys (requires root): "
            f"sudo rm -rf /var/lib/bluetooth/*/{mac} "
            f"/var/lib/bluetooth/*/cache/{mac} && sudo systemctl restart bluetooth"
        )

    async def sample_connected(
        self,
        path: str,
        *,
        duration: float = 5.0,
        interval: float = 0.25,
    ) -> StabilitySample:
        samples: list[bool] = []
        end = time.monotonic() + duration
        while time.monotonic() < end:
            try:
                device = await self.refresh_device(path)
                samples.append(bool(device.connected))
            except Exception:  # noqa: BLE001
                samples.append(False)
            await asyncio.sleep(interval)
        if not samples:
            return StabilitySample([], flap=False, final_connected=False)
        # Flap: saw both True and False, or transitions > 1
        transitions = sum(1 for a, b in zip(samples, samples[1:]) if a != b)
        flap = transitions >= 2 or (True in samples and False in samples and transitions >= 1 and duration >= 1.0)
        # Stricter: if connected ratio not near 1.0 after any True
        if samples[0] and not samples[-1]:
            flap = True
        if True in samples and False in samples and transitions >= 2:
            flap = True
        return StabilitySample(samples=samples, flap=flap, final_connected=samples[-1])

    async def set_wake_allowed(self, path: str, allowed: bool = True) -> None:
        _, props = await self._device_iface(path)
        try:
            await props.call_set(DEVICE_IFACE, "WakeAllowed", Variant("b", allowed))
        except Exception as exc:  # noqa: BLE001
            LOG.debug("WakeAllowed set failed: %s", exc)

    async def connect_raw(self, path: str, *, timeout: float = 20.0) -> None:
        device = await self.refresh_device(path)
        if device.connected:
            return
        iface, _ = await self._device_iface(path)
        try:
            await asyncio.wait_for(iface.call_connect(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            # Abort stuck ACL/LE attempts so later strategies are not "In Progress"
            await self.abort_connection_attempt(path)
            raise ConnectError(
                f"Connect timed out after {timeout:.0f}s",
                reason="connect_timeout",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            reason, msg = parse_bluez_error(exc)
            if reason == "already_connected":
                return
            if reason == "in_progress" or "busy" in msg.lower():
                await self.abort_connection_attempt(path)
            raise ConnectError(f"Connect failed: {msg}", reason=reason) from exc

    async def abort_connection_attempt(self, path: str) -> str:
        """Best-effort cancel of an in-flight Connect (Disconnect / CancelPairing)."""
        notes: list[str] = []
        iface, _ = await self._device_iface(path)
        for label, method_name in (
            ("Disconnect", "call_disconnect"),
            ("CancelPairing", "call_cancel_pairing"),
        ):
            try:
                method = getattr(iface, method_name)
                await asyncio.wait_for(method(), timeout=3.0)
                notes.append(f"{label}=ok")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{label}={exc.__class__.__name__}")
        await asyncio.sleep(0.6)
        return ", ".join(notes)

    async def connect_profile(self, path: str, uuid: str, *, timeout: float = 15.0) -> None:
        iface, _ = await self._device_iface(path)
        try:
            await asyncio.wait_for(iface.call_connect_profile(uuid), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ConnectError(
                f"ConnectProfile({uuid}) timed out after {timeout:.0f}s",
                reason="profile_timeout",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            reason, msg = parse_bluez_error(exc)
            # Map page-timeout etc.
            if "page-timeout" in msg.lower():
                reason = "hid_page_timeout"
            raise ConnectError(f"ConnectProfile({uuid}) failed: {msg}", reason=reason) from exc

    async def wait_services_resolved(self, path: str, *, timeout: float = 8.0) -> DeviceInfo:
        deadline = time.monotonic() + timeout
        last = await self.refresh_device(path)
        while time.monotonic() < deadline:
            last = await self.refresh_device(path)
            if last.services_resolved and last.connected:
                return last
            if not last.connected:
                return last
            await asyncio.sleep(0.25)
        return last

    async def wake_scan_for(self, address: str, *, timeout: float = 6.0) -> bool:
        """
        Brief LE discovery to coax advertising / RSSI updates for a bonded device.
        Returns True if the device showed RSSI or Services/Manufacturer updates.
        """
        mac = normalize_mac(address)
        await self.set_powered(True)
        adapter_iface, props, adapter_path = await self._adapter_iface()
        # Prefer LE dual transport filter when supported
        try:
            await adapter_iface.call_set_discovery_filter(
                {
                    "Transport": Variant("s", "le"),
                    "DuplicateData": Variant("b", True),
                }
            )
        except Exception as exc:  # noqa: BLE001
            LOG.debug("SetDiscoveryFilter: %s", exc)

        discovering = await props.call_get(ADAPTER_IFACE, "Discovering")
        discovering = discovering.value if hasattr(discovering, "value") else discovering
        started = False
        if not discovering:
            try:
                await adapter_iface.call_start_discovery()
                started = True
            except Exception as exc:  # noqa: BLE001
                LOG.debug("StartDiscovery: %s", exc)

        seen = False
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    device = await self.get_device(mac)
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(0.3)
                    continue
                if device.rssi is not None or device.connected:
                    seen = True
                    break
                await asyncio.sleep(0.3)
        finally:
            if started:
                try:
                    await adapter_iface.call_stop_discovery()
                except Exception:  # noqa: BLE001
                    pass
            # clear filter best-effort
            try:
                await adapter_iface.call_set_discovery_filter({})
            except Exception:  # noqa: BLE001
                pass
        return seen

    async def connect(
        self,
        query: str,
        *,
        wait_stable: float = 5.0,
        verify_usable: bool = True,
    ) -> ConnectResult:
        from bt_resolver.profiles import detect_profile, verify_usable_connection

        await self.ensure_agent()
        await self.set_powered(True)
        device = await self.get_device(query)
        messages: list[str] = []

        if not device.connected:
            iface, _ = await self._device_iface(device.path)
            try:
                await asyncio.wait_for(iface.call_connect(), timeout=20.0)
            except asyncio.TimeoutError as exc:
                raise ConnectError(
                    f"Connect timed out after 20s for {device.display_name}",
                    reason="connect_timeout",
                ) from exc
            except Exception as exc:  # noqa: BLE001
                reason, msg = parse_bluez_error(exc)
                if reason != "already_connected":
                    raise ConnectError(f"Connect failed: {msg}", reason=reason) from exc
            messages.append("Connect requested")
        else:
            messages.append("Already connected")

        stability = await self.sample_connected(device.path, duration=max(0.5, wait_stable))
        device = await self.refresh_device(device.path)
        profile = detect_profile(device)

        if stability.flap:
            raise ConnectFlapError(
                f"{device.display_name}: connection flapping "
                f"(connected {stability.connected_ratio:.0%} over {wait_stable}s). "
                f"Often HID/auth failure (Xbox logo blink loop).",
                reason="connect_flap",
            )

        if not stability.final_connected:
            raise ConnectError(
                f"{device.display_name}: not connected after connect attempt",
                reason="connect_failed",
            )

        usable = True
        reason = "ok"
        if verify_usable:
            ok, detail, fail_reason = verify_usable_connection(device)
            usable = ok
            messages.append(detail)
            if not ok:
                reason = fail_reason
                raise UsableConnectionError(
                    f"{device.display_name}: connected but not usable — {detail}",
                    reason=fail_reason,
                )

        return ConnectResult(
            device=device,
            stable=True,
            stability=stability,
            usable=usable,
            profile=profile.name,
            messages=messages,
            reason=reason,
        )


@asynccontextmanager
async def open_manager() -> AsyncIterator[BluetoothManager]:
    mgr = await BluetoothManager.connect_bus()
    try:
        yield mgr
    finally:
        await mgr.close()
