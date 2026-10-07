"""Optional BlueZ pairing agent (NoInputNoOutput) when no desktop agent is present."""

from __future__ import annotations

import logging

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

from bt_resolver.errors import AgentError

LOG = logging.getLogger(__name__)

AGENT_PATH = "/org/bt_resolver/agent"
CAPABILITY = "NoInputNoOutput"


class PairingAgent(ServiceInterface):
    """Minimal Just Works agent for headless pairing."""

    def __init__(self) -> None:
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):  # noqa: N802
        LOG.debug("Agent released")

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: N802
        return "0000"

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: N802
        LOG.info("PIN for %s: %s", device, pincode)

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: N802
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: N802
        LOG.info("Passkey for %s: %06d (entered %s)", device, passkey, entered)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: N802
        LOG.info("Confirming passkey for %s: %06d", device, passkey)

    @method()
    def RequestAuthorization(self, device: "o"):  # noqa: N802
        return None

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: N802
        return None

    @method()
    def Cancel(self):  # noqa: N802
        return None


async def try_register_agent(bus: MessageBus) -> tuple[bool, str]:
    """
    Register our agent as default if possible.

    Returns (registered, note). When a desktop agent already owns the role,
    BlueZ may reject RegisterAgent / RequestDefaultAgent — that is OK; we
    leave Plasma/GNOME alone.
    """
    introspection = await bus.introspect("org.bluez", "/org/bluez")
    root = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
    agent_mgr = root.get_interface("org.bluez.AgentManager1")

    agent = PairingAgent()
    bus.export(AGENT_PATH, agent)

    try:
        await agent_mgr.call_register_agent(AGENT_PATH, CAPABILITY)
    except Exception as exc:  # noqa: BLE001
        try:
            bus.unexport(AGENT_PATH)
        except Exception:  # noqa: BLE001
            pass
        msg = str(exc)
        if "AlreadyExists" in msg or "in use" in msg.lower():
            return False, "Desktop/system agent already registered; using existing agent"
        return False, f"Could not register agent: {msg}"

    try:
        await agent_mgr.call_request_default_agent(AGENT_PATH)
    except Exception as exc:  # noqa: BLE001
        return True, f"Agent registered but not default: {exc}"

    return True, "Registered NoInputNoOutput agent as default"


async def unregister_agent(bus: MessageBus) -> None:
    try:
        introspection = await bus.introspect("org.bluez", "/org/bluez")
        root = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
        agent_mgr = root.get_interface("org.bluez.AgentManager1")
        await agent_mgr.call_unregister_agent(AGENT_PATH)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Unregister agent: %s", exc)
    try:
        bus.unexport(AGENT_PATH)
    except Exception:  # noqa: BLE001
        pass


def require_agent_or_raise(registered: bool, note: str, *, force: bool) -> None:
    if registered or not force:
        return
    raise AgentError(note or "No pairing agent available", reason="agent_error")


def parse_bluez_error(exc: BaseException) -> tuple[str, str]:
    """Return (reason, message) from a BlueZ D-Bus exception."""
    text = str(exc)
    low = text.lower()
    if "page-timeout" in low or "br-connection-page-timeout" in low:
        return "hid_page_timeout", text
    if "authentication" in low or "org.bluez.error.authenticationfailed" in low:
        return "auth_failed", text
    if "already connected" in low:
        return "already_connected", text
    if "in progress" in low:
        return "in_progress", text
    if "busy" in low or "br-connection-busy" in low:
        return "connection_busy", text
    if "failed" in low:
        return "connect_failed", text
    return "dbus_error", text
