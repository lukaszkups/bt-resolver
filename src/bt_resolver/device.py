"""Device model and MAC/name resolution helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

# Common Bluetooth UUIDs
UUID_HID = "00001812-0000-1000-8000-00805f9b34fb"  # HID over GATT (BLE)
UUID_HID_BREDR = "00001124-0000-1000-8000-00805f9b34fb"  # classic HID (PS4 DualShock)
UUID_A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"
UUID_A2DP_SOURCE = "0000110a-0000-1000-8000-00805f9b34fb"
UUID_HFP_HF = "0000111e-0000-1000-8000-00805f9b34fb"
UUID_HFP_AG = "0000111f-0000-1000-8000-00805f9b34fb"
UUID_AUDIO_SINK = "0000110b-0000-1000-8000-00805f9b34fb"


def normalize_mac(value: str) -> str:
    value = value.strip().upper().replace("-", ":")
    if not MAC_RE.match(value):
        raise ValueError(f"Invalid MAC address: {value}")
    return value


def is_mac(value: str) -> bool:
    try:
        normalize_mac(value)
        return True
    except ValueError:
        return False


def mac_to_path_segment(mac: str) -> str:
    return "dev_" + normalize_mac(mac).replace(":", "_")


@dataclass
class DeviceInfo:
    path: str
    address: str
    name: str = ""
    alias: str = ""
    icon: str = ""
    paired: bool = False
    bonded: bool = False
    trusted: bool = False
    connected: bool = False
    blocked: bool = False
    services_resolved: bool = False
    wake_allowed: bool = False
    address_type: str = ""
    rssi: int | None = None
    uuids: list[str] = field(default_factory=list)
    modalias: str = ""
    appearance: int = 0
    adapter_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def display_name(self) -> str:
        return self.alias or self.name or self.address

    def has_uuid(self, uuid: str) -> bool:
        target = uuid.lower()
        return any(u.lower() == target for u in self.uuids)

    def matches_query(self, query: str) -> bool:
        q = query.strip().lower()
        if is_mac(query):
            return self.address.upper() == normalize_mac(query)
        return q in self.display_name.lower() or q in self.name.lower()

    @classmethod
    def from_props(cls, path: str, props: dict[str, Any]) -> DeviceInfo:
        plain = {k: _unwrap(v) for k, v in props.items()}
        uuids = list(plain.get("UUIDs") or [])
        adapter = str(plain.get("Adapter") or "")
        return cls(
            path=path,
            address=str(plain.get("Address") or ""),
            name=str(plain.get("Name") or ""),
            alias=str(plain.get("Alias") or ""),
            icon=str(plain.get("Icon") or ""),
            paired=bool(plain.get("Paired", False)),
            bonded=bool(plain.get("Bonded", plain.get("Paired", False))),
            trusted=bool(plain.get("Trusted", False)),
            connected=bool(plain.get("Connected", False)),
            blocked=bool(plain.get("Blocked", False)),
            services_resolved=bool(plain.get("ServicesResolved", False)),
            wake_allowed=bool(plain.get("WakeAllowed", False)),
            address_type=str(plain.get("AddressType") or ""),
            rssi=int(plain["RSSI"]) if plain.get("RSSI") is not None else None,
            uuids=uuids,
            modalias=str(plain.get("Modalias") or ""),
            appearance=int(plain.get("Appearance") or 0),
            adapter_path=adapter,
            raw=plain,
        )


def _unwrap(value: Any) -> Any:
    if hasattr(value, "value"):
        return _unwrap(value.value)
    if isinstance(value, (list, tuple)):
        return type(value)(_unwrap(v) for v in value)
    return value


def _name_match_rank(device: DeviceInfo, query: str) -> int:
    """
    Higher is better. Multi-word queries must match the full name/alias
    (so 'Wireless Controller' does not hit 'Xbox Wireless Controller').
    """
    q = query.lower().strip()
    if not q:
        return 0
    q_tokens = q.split()
    best = 0
    for raw in (device.display_name, device.name, device.alias):
        n = (raw or "").lower().strip()
        if not n:
            continue
        if n == q:
            best = max(best, 100)
            continue
        n_tokens = n.split()
        if n_tokens == q_tokens:
            best = max(best, 100)
            continue
        # Single-token query: allow unique token / prefix match
        if len(q_tokens) == 1:
            token = q_tokens[0]
            if token in n_tokens:
                best = max(best, 70)
            elif any(t.startswith(token) for t in n_tokens if len(token) >= 3):
                best = max(best, 40)
    return best


def resolve_device(devices: list[DeviceInfo], query: str) -> DeviceInfo:
    """Resolve by exact MAC or unique fuzzy name match."""
    from bt_resolver.errors import DeviceNotFoundError

    query = query.strip()
    if not query:
        raise DeviceNotFoundError("Empty device query")

    if is_mac(query):
        mac = normalize_mac(query)
        for d in devices:
            if d.address.upper() == mac:
                return d
        raise DeviceNotFoundError(f"No device with address {mac}")

    ranked = [( _name_match_rank(d, query), d) for d in devices]
    ranked = [(score, d) for score, d in ranked if score > 0]
    if not ranked:
        raise DeviceNotFoundError(f"No device matching '{query}'")
    ranked.sort(key=lambda item: item[0], reverse=True)
    top = ranked[0][0]
    winners = [d for score, d in ranked if score == top]
    # Prefer exact (100) only; reject weak sole matches that are too loose
    if top < 70 and len(query.split()) > 1:
        raise DeviceNotFoundError(f"No device matching '{query}'")
    if len(winners) == 1:
        return winners[0]
    addrs = ", ".join(f"{d.display_name} ({d.address})" for d in winners)
    raise DeviceNotFoundError(f"Ambiguous name '{query}': {addrs}")
