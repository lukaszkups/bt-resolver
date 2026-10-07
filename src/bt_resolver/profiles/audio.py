from __future__ import annotations

import shutil
import subprocess

from bt_resolver.device import UUID_A2DP_SINK, UUID_A2DP_SOURCE, UUID_HFP_AG, UUID_HFP_HF, DeviceInfo
from bt_resolver.profiles.base import Profile


class AudioProfile(Profile):
    name = "audio"

    def matches(self, device: DeviceInfo) -> bool:
        if device.icon in {"audio-headset", "audio-headphones", "audio-card"}:
            return True
        return any(
            device.has_uuid(u)
            for u in (UUID_A2DP_SINK, UUID_A2DP_SOURCE, UUID_HFP_HF, UUID_HFP_AG)
        )

    def verify(self, device: DeviceInfo) -> tuple[bool, str, str]:
        if not device.connected:
            return False, "Audio device not connected", "not_connected"

        has_a2dp = device.has_uuid(UUID_A2DP_SINK) or device.has_uuid(UUID_A2DP_SOURCE)
        sink = _find_pulse_sink(device)
        if sink:
            return True, f"Connected with audio sink '{sink}'", "ok"
        if has_a2dp:
            return (
                False,
                "A2DP UUID present but no PulseAudio/PipeWire sink found yet "
                "(wait a few seconds or check WirePlumber/Pulse)",
                "no_audio_sink",
            )
        return (
            False,
            "Connected but no A2DP/HFP UUID and no audio sink — profile may not have started",
            "no_audio_profile",
        )


def _find_pulse_sink(device: DeviceInfo) -> str | None:
    """Best-effort match of a PipeWire/Pulse sink to this device."""
    needles = [
        device.address.lower().replace(":", "_"),
        device.address.lower().replace(":", ""),
        device.display_name.lower(),
    ]
    for cmd in (
        ["pactl", "list", "short", "sinks"],
        ["pactl", "list", "short", "cards"],
    ):
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=3)
        except (subprocess.SubprocessError, OSError):
            continue
        low = out.lower()
        for needle in needles:
            if needle and needle in low:
                for line in out.splitlines():
                    if needle in line.lower():
                        return line.split()[1] if len(line.split()) > 1 else line.strip()
    return None
