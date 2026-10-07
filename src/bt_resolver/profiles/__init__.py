"""Device profiles for usable-connection verification."""

from __future__ import annotations

from bt_resolver.device import DeviceInfo
from bt_resolver.profiles.audio import AudioProfile
from bt_resolver.profiles.base import Profile
from bt_resolver.profiles.dualshock import DualShockProfile
from bt_resolver.profiles.generic import GenericProfile
from bt_resolver.profiles.xbox import XboxProfile

PROFILES: list[Profile] = [
    XboxProfile(),
    DualShockProfile(),
    AudioProfile(),
    GenericProfile(),
]


def detect_profile(device: DeviceInfo) -> Profile:
    for profile in PROFILES:
        if profile.matches(device):
            return profile
    return GenericProfile()


def verify_usable_connection(device: DeviceInfo) -> tuple[bool, str, str]:
    """Return (ok, detail, fail_reason)."""
    profile = detect_profile(device)
    return profile.verify(device)


__all__ = [
    "Profile",
    "detect_profile",
    "verify_usable_connection",
    "XboxProfile",
    "DualShockProfile",
    "AudioProfile",
    "GenericProfile",
]
