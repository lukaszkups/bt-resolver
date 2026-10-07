from bt_resolver.device import UUID_A2DP_SINK, UUID_HID, UUID_HID_BREDR, DeviceInfo
from bt_resolver.profiles import detect_profile, verify_usable_connection
from bt_resolver.profiles.xbox import XboxProfile


def test_detect_dualshock_by_modalias():
    d = DeviceInfo(
        path="/p",
        address="40:1B:5F:2C:CF:EA",
        name="Wireless Controller",
        icon="input-gaming",
        modalias="usb:v054Cp05C4d0100",
        uuids=[UUID_HID_BREDR],
    )
    assert detect_profile(d).name == "dualshock"


def test_detect_xbox_by_name():
    d = DeviceInfo(
        path="/x",
        address="44:16:22:DF:3D:22",
        name="Xbox Wireless Controller",
        connected=True,
        uuids=[UUID_HID],
    )
    assert detect_profile(d).name == "xbox"


def test_detect_audio_by_uuid():
    d = DeviceInfo(
        path="/a",
        address="11:22:33:44:55:66",
        name="Headphones",
        icon="audio-headset",
        connected=True,
        uuids=[UUID_A2DP_SINK],
    )
    assert detect_profile(d).name == "audio"


def test_generic_fallback():
    d = DeviceInfo(path="/g", address="00:11:22:33:44:55", name="Thing")
    assert detect_profile(d).name == "generic"


def test_xbox_verify_no_hid(monkeypatch):
    d = DeviceInfo(
        path="/x",
        address="44:16:22:DF:3D:22",
        name="Xbox Wireless Controller",
        connected=True,
        uuids=[],
    )
    monkeypatch.setattr("bt_resolver.profiles.xbox.find_xbox_input_nodes", lambda: [])
    monkeypatch.setattr("bt_resolver.profiles.xbox.find_xbox_hid_driver", lambda: None)
    monkeypatch.setattr("bt_resolver.profiles.xbox.module_loaded", lambda _n: False)
    ok, detail, reason = XboxProfile().verify(d)
    assert not ok
    assert reason == "no_hid"
    assert "HID" in detail


def test_xbox_verify_usable(monkeypatch):
    d = DeviceInfo(
        path="/x",
        address="44:16:22:DF:3D:22",
        name="Xbox Wireless Controller",
        connected=True,
        uuids=[UUID_HID],
    )
    monkeypatch.setattr(
        "bt_resolver.profiles.xbox.find_xbox_input_nodes",
        lambda: ["/dev/input/js0"],
    )
    monkeypatch.setattr("bt_resolver.profiles.xbox.find_xbox_hid_driver", lambda: "hid-xpadneo")
    monkeypatch.setattr("bt_resolver.profiles.xbox.module_loaded", lambda _n: True)
    ok, detail, reason = verify_usable_connection(d)
    assert ok
    assert reason == "ok"
    assert "js0" in detail


def test_generic_connected_ok():
    d = DeviceInfo(
        path="/g",
        address="00:11:22:33:44:55",
        name="Mouse",
        connected=True,
        paired=True,
    )
    ok, _, reason = verify_usable_connection(d)
    assert ok
    assert reason == "ok"
