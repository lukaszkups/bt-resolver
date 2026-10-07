from __future__ import annotations

from bt_resolver.device import DeviceInfo
from bt_resolver.profiles.base import Profile


class GenericProfile(Profile):
    name = "generic"

    def matches(self, device: DeviceInfo) -> bool:
        return True

    def verify(self, device: DeviceInfo) -> tuple[bool, str, str]:
        if not device.connected:
            return False, "Device reports Connected=no", "not_connected"
        if device.paired or device.trusted:
            return True, "Connected (generic device; no profile-specific endpoints required)", "ok"
        return True, "Connected (unpaired session; may be transient)", "ok"
