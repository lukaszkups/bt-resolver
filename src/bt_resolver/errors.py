"""Typed failures with short CLI-friendly reasons."""

from __future__ import annotations


class BtResolverError(Exception):
    """Base error with a stable reason code."""

    reason: str = "error"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason


class AdapterError(BtResolverError):
    reason = "adapter_error"


class DeviceNotFoundError(BtResolverError):
    reason = "device_not_found"


class ConnectError(BtResolverError):
    reason = "connect_failed"


class ConnectFlapError(ConnectError):
    reason = "connect_flap"


class UsableConnectionError(ConnectError):
    reason = "not_usable"


class PairError(BtResolverError):
    reason = "pair_failed"


class AgentError(BtResolverError):
    reason = "agent_error"


class PermissionError_(BtResolverError):
    reason = "permission_denied"
