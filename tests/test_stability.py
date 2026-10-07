from bt_resolver.manager import StabilitySample


def test_stability_ratio():
    s = StabilitySample(samples=[True, True, False, True], flap=True, final_connected=True)
    assert s.connected_ratio == 0.75


def test_agent_error_parsing():
    from bt_resolver.agent import parse_bluez_error

    reason, _ = parse_bluez_error(Exception("br-connection-page-timeout"))
    assert reason == "hid_page_timeout"
    reason, _ = parse_bluez_error(Exception("org.bluez.Error.AuthenticationFailed"))
    assert reason == "auth_failed"
    reason, _ = parse_bluez_error(Exception("br-connection-busy"))
    assert reason == "connection_busy"
