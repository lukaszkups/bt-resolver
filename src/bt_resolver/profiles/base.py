from __future__ import annotations

from abc import ABC, abstractmethod

from bt_resolver.device import DeviceInfo


class Profile(ABC):
    name: str = "generic"

    @abstractmethod
    def matches(self, device: DeviceInfo) -> bool:
        raise NotImplementedError

    @abstractmethod
    def verify(self, device: DeviceInfo) -> tuple[bool, str, str]:
        """Return (ok, detail_message, fail_reason)."""
        raise NotImplementedError
