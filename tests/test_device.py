import pytest

from bt_resolver.device import DeviceInfo, is_mac, normalize_mac, resolve_device
from bt_resolver.errors import DeviceNotFoundError


def test_normalize_mac():
    assert normalize_mac("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert is_mac("44:16:22:DF:3D:22")
    assert not is_mac("Xbox")


def test_resolve_by_mac():
    devices = [
        DeviceInfo(path="/a", address="AA:BB:CC:DD:EE:FF", name="Pad"),
        DeviceInfo(path="/b", address="11:22:33:44:55:66", name="Buds"),
    ]
    d = resolve_device(devices, "aa:bb:cc:dd:ee:ff")
    assert d.name == "Pad"


def test_resolve_by_unique_partial_name():
    devices = [
        DeviceInfo(path="/a", address="AA:BB:CC:DD:EE:FF", name="Xbox Wireless Controller"),
        DeviceInfo(path="/b", address="11:22:33:44:55:66", name="WH-1000XM4"),
    ]
    d = resolve_device(devices, "xbox")
    assert d.address == "AA:BB:CC:DD:EE:FF"


def test_wireless_controller_does_not_match_xbox():
    devices = [
        DeviceInfo(path="/a", address="44:16:22:DF:3D:22", name="Xbox Wireless Controller"),
        DeviceInfo(path="/b", address="40:1B:5F:2C:CF:EA", name="Wireless Controller"),
    ]
    d = resolve_device(devices, "Wireless Controller")
    assert d.address == "40:1B:5F:2C:CF:EA"


def test_wireless_controller_absent_does_not_hit_xbox():
    devices = [
        DeviceInfo(path="/a", address="44:16:22:DF:3D:22", name="Xbox Wireless Controller"),
    ]
    with pytest.raises(DeviceNotFoundError):
        resolve_device(devices, "Wireless Controller")


def test_resolve_ambiguous():
    devices = [
        DeviceInfo(path="/a", address="AA:BB:CC:DD:EE:01", name="Headset Left"),
        DeviceInfo(path="/b", address="AA:BB:CC:DD:EE:02", name="Headset Right"),
    ]
    with pytest.raises(DeviceNotFoundError, match="Ambiguous"):
        resolve_device(devices, "Headset")


def test_resolve_missing():
    with pytest.raises(DeviceNotFoundError):
        resolve_device([], "missing")


def test_from_props_unwraps_variants():
    class V:
        def __init__(self, value):
            self.value = value

    d = DeviceInfo.from_props(
        "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
        {
            "Address": V("AA:BB:CC:DD:EE:FF"),
            "Name": V("Test"),
            "Alias": V("Test"),
            "Paired": V(True),
            "Connected": V(False),
            "UUIDs": V(["00001812-0000-1000-8000-00805f9b34fb"]),
            "Trusted": V(True),
            "Blocked": V(False),
            "Icon": V("input-gaming"),
            "Modalias": V("usb:v045Ep0B13d0001"),
            "Appearance": V(964),
            "Adapter": V("/org/bluez/hci0"),
        },
    )
    assert d.address == "AA:BB:CC:DD:EE:FF"
    assert d.paired is True
    assert d.has_uuid("00001812-0000-1000-8000-00805f9b34fb")
