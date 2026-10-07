"""bt-resolver command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Sequence

from bt_resolver import __version__
from bt_resolver.errors import BtResolverError
from bt_resolver.manager import open_manager
from bt_resolver.diagnose import run_diagnose, run_doctor
from bt_resolver.profiles import detect_profile
from bt_resolver.resolve import resolve_connection


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bt-resolver",
        description=(
            "Verify usable Bluetooth connections and diagnose BlueZ failures "
            "(not a Plasma/Blueman replacement)."
        ),
    )
    p.add_argument("--version", action="version", version=f"bt-resolver {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("adapters", help="List Bluetooth adapters")

    sc = sub.add_parser("scan", help="Scan for devices")
    sc.add_argument("--timeout", type=float, default=15.0)

    dv = sub.add_parser("devices", help="List known devices")
    dv.add_argument("--connected", action="store_true")
    dv.add_argument("--paired", action="store_true")

    info = sub.add_parser("info", help="Show device details")
    info.add_argument("device", help="MAC address or name")

    pair = sub.add_parser("pair", help="Pair (and trust) a device")
    pair.add_argument("device")
    pair.add_argument("--timeout", type=float, default=60.0)

    conn = sub.add_parser("connect", help="Connect and verify usable link")
    conn.add_argument("device")
    conn.add_argument("--wait-stable", type=float, default=5.0, dest="wait_stable")
    conn.add_argument(
        "--no-verify",
        action="store_true",
        help="Only check Connected stability, skip profile usable checks",
    )

    reso = sub.add_parser(
        "resolve",
        help="Low-level multi-strategy connect (wake scan, ConnectProfile, cycle)",
    )
    reso.add_argument("device")
    reso.add_argument("--wait-stable", type=float, default=4.0, dest="wait_stable")
    reso.add_argument("--wake-scan", type=float, default=6.0, dest="wake_scan")
    reso.add_argument("--usable-wait", type=float, default=8.0, dest="usable_wait")

    disc = sub.add_parser("disconnect", help="Disconnect a device")
    disc.add_argument("device")

    rem = sub.add_parser("remove", help="Remove device bond from BlueZ")
    rem.add_argument("device")
    rem.add_argument(
        "--purge-cache",
        action="store_true",
        help="Print root commands to wipe leftover /var/lib/bluetooth keys",
    )

    trust = sub.add_parser("trust", help="Mark device trusted")
    trust.add_argument("device")

    diag = sub.add_parser("diagnose", help="Full host+device diagnostic report")
    diag.add_argument("device", nargs="?", default=None)
    diag.add_argument("--sample", type=float, default=5.0, help="Stability sample seconds")

    sub.add_parser("doctor", help="Host readiness checks only")

    sub.add_parser("tray", help="Launch system tray GUI (requires PySide6)")
    return p


def _print_devices(devices, *, connected: bool = False, paired: bool = False) -> None:
    for d in devices:
        if connected and not d.connected:
            continue
        if paired and not d.paired:
            continue
        profile = detect_profile(d).name
        flags = []
        if d.connected:
            flags.append("connected")
        if d.paired:
            flags.append("paired")
        if d.bonded:
            flags.append("bonded")
        if d.trusted:
            flags.append("trusted")
        flag_s = ",".join(flags) if flags else "-"
        print(f"{d.address}  {d.display_name}  [{profile}]  {flag_s}")


async def _run(args: argparse.Namespace) -> int:
    async with open_manager() as mgr:
        if args.command == "adapters":
            for a in await mgr.list_adapters():
                print(
                    f"{a.address}  {a.name or a.path}  powered={a.powered} "
                    f"pairable={a.pairable} discovering={a.discovering}"
                )
            return 0

        if args.command == "scan":
            devices = await mgr.scan(timeout=args.timeout)
            _print_devices(devices)
            return 0

        if args.command == "devices":
            devices = await mgr.list_devices()
            _print_devices(devices, connected=args.connected, paired=args.paired)
            return 0

        if args.command == "info":
            d = await mgr.get_device(args.device)
            profile = detect_profile(d).name
            print(f"Address:   {d.address}")
            print(f"Name:      {d.display_name}")
            print(f"Icon:      {d.icon}")
            print(f"Profile:   {profile}")
            print(f"Paired:    {d.paired}")
            print(f"Bonded:    {d.bonded}")
            print(f"Trusted:   {d.trusted}")
            print(f"Connected: {d.connected}")
            print(f"Services:  {d.services_resolved}")
            print(f"WakeAllow: {d.wake_allowed}")
            print(f"AddrType:  {d.address_type or '-'}")
            print(f"RSSI:      {d.rssi if d.rssi is not None else '-'}")
            print(f"Modalias:  {d.modalias or '-'}")
            print(f"UUIDs ({len(d.uuids)}):")
            for u in d.uuids:
                print(f"  {u}")
            return 0

        if args.command == "pair":
            note = await mgr.ensure_agent()
            print(note)
            d = await mgr.pair(args.device, timeout=args.timeout)
            print(f"Paired/trusted: {d.display_name} ({d.address})")
            return 0

        if args.command == "connect":
            result = await mgr.connect(
                args.device,
                wait_stable=args.wait_stable,
                verify_usable=not args.no_verify,
            )
            d = result.device
            print(f"Connected: {d.display_name} ({d.address})")
            print(f"Profile:   {result.profile}")
            print(f"Stable:    {result.stable} (ratio={result.stability.connected_ratio:.0%})")
            print(f"Usable:    {result.usable}")
            for m in result.messages:
                print(f"  - {m}")
            return 0

        if args.command == "resolve":
            result = await resolve_connection(
                mgr,
                args.device,
                wait_stable=args.wait_stable,
                wake_scan=args.wake_scan,
                usable_wait=args.usable_wait,
            )
            sys.stdout.write(result.format())
            if not result.usable:
                print(f"error[{result.reason}]: resolve did not reach a usable connection", file=sys.stderr)
                return 1
            return 0

        if args.command == "disconnect":
            d = await mgr.disconnect(args.device)
            print(f"Disconnected: {d.display_name} ({d.address})")
            return 0

        if args.command == "remove":
            note = await mgr.remove(args.device, purge_cache=args.purge_cache)
            print(note)
            return 0

        if args.command == "trust":
            d = await mgr.trust(args.device)
            print(f"Trusted: {d.display_name} ({d.address})")
            return 0

        if args.command == "doctor":
            report = await run_doctor(mgr)
            sys.stdout.write(report.format())
            return 0 if report.ok else 1

        if args.command == "diagnose":
            report = await run_diagnose(mgr, args.device, sample_seconds=args.sample)
            sys.stdout.write(report.format())
            return 0 if report.ok else 1

    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "tray":
        from bt_resolver.tray import main as tray_main

        return tray_main()
    try:
        return asyncio.run(_run(args))
    except BtResolverError as exc:
        print(f"error[{exc.reason}]: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
