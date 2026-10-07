from bt_resolver.cli import build_parser


def test_parser_connect_flags():
    p = build_parser()
    args = p.parse_args(["connect", "Xbox", "--wait-stable", "3", "--no-verify"])
    assert args.command == "connect"
    assert args.device == "Xbox"
    assert args.wait_stable == 3.0
    assert args.no_verify is True


def test_parser_diagnose_optional_device():
    p = build_parser()
    args = p.parse_args(["diagnose"])
    assert args.device is None
    args = p.parse_args(["diagnose", "44:16:22:DF:3D:22", "--sample", "2"])
    assert args.device == "44:16:22:DF:3D:22"
    assert args.sample == 2.0


def test_parser_remove_purge():
    p = build_parser()
    args = p.parse_args(["remove", "AA:BB:CC:DD:EE:FF", "--purge-cache"])
    assert args.purge_cache is True


def test_parser_devices_filters():
    p = build_parser()
    args = p.parse_args(["devices", "--paired", "--connected"])
    assert args.paired and args.connected


def test_all_commands_registered():
    p = build_parser()
    # argparse stores subparsers on _subparsers
    choices = set()
    for action in p._actions:
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            choices |= set(action.choices)
    expected = {
        "adapters",
        "scan",
        "devices",
        "info",
        "pair",
        "connect",
        "resolve",
        "disconnect",
        "remove",
        "trust",
        "diagnose",
        "doctor",
        "tray",
    }
    assert expected <= choices


def test_parser_resolve_flags():
    p = build_parser()
    args = p.parse_args(
        ["resolve", "Xbox", "--wake-scan", "3", "--usable-wait", "5", "--wait-stable", "2"]
    )
    assert args.command == "resolve"
    assert args.wake_scan == 3.0
    assert args.usable_wait == 5.0
    assert args.wait_stable == 2.0
