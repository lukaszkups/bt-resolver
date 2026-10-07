from bt_resolver.device import UUID_A2DP_SINK, UUID_HID, DeviceInfo
from bt_resolver.resolve import _profile_uuids


def test_profile_uuids_xbox():
    d = DeviceInfo(
        path="/x",
        address="44:16:22:DF:3D:22",
        name="Xbox Wireless Controller",
        uuids=[UUID_HID],
    )
    assert _profile_uuids(d, "xbox") == [UUID_HID]


def test_profile_uuids_audio():
    d = DeviceInfo(
        path="/a",
        address="11:22:33:44:55:66",
        name="Headphones",
        uuids=[UUID_A2DP_SINK],
    )
    assert UUID_A2DP_SINK in _profile_uuids(d, "audio")
