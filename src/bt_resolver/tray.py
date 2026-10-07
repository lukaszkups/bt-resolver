"""System tray GUI for bt-resolver (Plasma / StatusNotifier friendly)."""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from bt_resolver import __version__
from bt_resolver.device import DeviceInfo
from bt_resolver.diagnose import run_diagnose, run_doctor
from bt_resolver.errors import BtResolverError
from bt_resolver.manager import open_manager
from bt_resolver.profiles import detect_profile
from bt_resolver.resolve import resolve_connection


def _in_pipx_venv() -> bool:
    parts = sys.prefix.replace("\\", "/").split("/")
    return "pipx" in parts and "venvs" in parts


def _qt_install_hint() -> str:
    if _in_pipx_venv():
        # pipx gives every app its own venv; PySide6 installed via
        # `pipx install PySide6` lands in a separate venv we can't see.
        return (
            "bt-resolver is installed with pipx, which isolates each app in its own\n"
            "virtualenv, so PySide6 must be added to bt-resolver's venv:\n"
            "  pipx inject bt-resolver PySide6\n"
            "  # or reinstall with the extra: pipx install --force '.[tray]'"
        )
    return (
        "Install with:\n"
        "  pip install 'bt-resolver[tray]'\n"
        "  # or: pip install PySide6"
    )


def _require_qt() -> None:
    try:
        import PySide6  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            f"Tray GUI requires PySide6 ({exc}).\n"
            f"Python: {sys.executable}\n"
            f"{_qt_install_hint()}"
        ) from exc


@dataclass
class DeviceSnapshot:
    address: str
    name: str
    connected: bool
    paired: bool
    profile: str

    @classmethod
    def from_device(cls, d: DeviceInfo) -> DeviceSnapshot:
        return cls(
            address=d.address,
            name=d.display_name,
            connected=d.connected,
            paired=d.paired,
            profile=detect_profile(d).name,
        )


def make_tray_icon(*, connected: bool = False):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    size = 128
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    fill = QColor("#2f8fd6") if connected else QColor("#5c6770")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawEllipse(16, 16, 96, 96)
    p.setPen(QColor("#f4f7fa"))
    font = p.font()
    font.setBold(True)
    font.setPixelSize(56)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "BT")
    p.end()
    return QIcon(pix)


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)


class TrayPanel:
    """Click-away tool window (not QMenu.popup — avoids Wayland grab / unthemed menus)."""

    def __init__(self, app: "TrayApp") -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QFrame,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QScrollArea,
            QSizePolicy,
            QVBoxLayout,
            QWidget,
        )

        self.app = app
        self._QPushButton = QPushButton

        self.win = QFrame()
        self.win.setObjectName("btResolverTrayPanel")
        self.win.setWindowTitle(f"bt-resolver {__version__}")
        self.win.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.win.setStyleSheet(
            """
            QFrame#btResolverTrayPanel {
                background: palette(window);
                border: 1px solid palette(mid);
                border-radius: 8px;
            }
            QPushButton {
                text-align: left;
                padding: 6px 10px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background: palette(midlight); }
            QPushButton:disabled { color: palette(mid); }
            QLabel#header { font-weight: 600; padding: 4px 2px; }
            QLabel#section { color: palette(mid); padding: 6px 2px 2px; font-size: 11px; }
            """
        )

        root = QVBoxLayout(self.win)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(4)

        self.header = QLabel(f"bt-resolver {__version__}")
        self.header.setObjectName("header")
        root.addWidget(self.header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumWidth(280)
        scroll.setMaximumHeight(420)
        self.body_host = QWidget()
        self.body = QVBoxLayout(self.body_host)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(2)
        scroll.setWidget(self.body_host)
        root.addWidget(scroll)

        actions = QHBoxLayout()
        for label, slot in (
            ("Refresh", app.refresh_devices),
            ("Scan", app.scan_devices),
            ("Doctor", app.run_doctor),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            actions.addWidget(b)
        root.addLayout(actions)

        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(app.quit)
        root.addWidget(quit_btn)

        self._filter = _make_panel_filter(self)
        self.win.installEventFilter(self._filter)

    def is_visible(self) -> bool:
        return self.win.isVisible()

    def hide(self) -> None:
        self.win.hide()

    def hide_if_focus_outside(self) -> None:
        from PySide6.QtWidgets import QApplication

        if not self.win.isVisible():
            return
        fw = QApplication.focusWidget()
        if fw is not None and (fw is self.win or self.win.isAncestorOf(fw)):
            return
        # Active popup/menu child counts as still interacting
        popup = QApplication.activePopupWidget()
        if popup is not None and (popup is self.win or self.win.isAncestorOf(popup)):
            return
        aw = QApplication.activeWindow()
        if aw is self.win:
            return
        self.hide()

    def rebuild(self, devices: list[DeviceSnapshot]) -> None:
        from PySide6.QtWidgets import QLabel

        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    child = lay.takeAt(0)
                    cw = child.widget()
                    if cw is not None:
                        cw.deleteLater()

        if not devices:
            self.body.addWidget(QLabel("No devices yet — try Scan"))
        else:
            self.body.addWidget(self._section("Devices"))
            for snap in devices:
                mark = "●" if snap.connected else ("◇" if not snap.paired else "○")
                unpaired = " · unpaired" if not snap.paired else ""
                title = QLabel(f"{mark}  {snap.name}  [{snap.profile}]{unpaired}")
                title.setWordWrap(True)
                self.body.addWidget(title)
                row = self._device_row(snap)
                self.body.addLayout(row)
        self.body.addStretch(1)

    def _section(self, text: str):
        from PySide6.QtWidgets import QLabel

        lab = QLabel(text.upper())
        lab.setObjectName("section")
        return lab

    def _device_row(self, snap: DeviceSnapshot):
        from PySide6.QtWidgets import QHBoxLayout, QPushButton

        row = QHBoxLayout()
        row.setSpacing(4)
        buttons: list[tuple[str, Callable[[], None], bool]] = []
        if not snap.paired:
            buttons.append(
                ("Pair", lambda a=snap.address, n=snap.name: self.app.pair_device(a, n), True)
            )
        buttons.extend(
            [
                ("Resolve", lambda a=snap.address, n=snap.name: self.app.resolve_device(a, n), True),
                ("Connect", lambda a=snap.address, n=snap.name: self.app.connect_device(a, n), True),
                (
                    "Disconnect",
                    lambda a=snap.address, n=snap.name: self.app.disconnect_device(a, n),
                    snap.connected,
                ),
                ("Diagnose", lambda a=snap.address, n=snap.name: self.app.diagnose_device(a, n), True),
                ("Forget", lambda a=snap.address, n=snap.name: self.app.forget_device(a, n), True),
            ]
        )
        for label, slot, enabled in buttons:
            b = QPushButton(label)
            b.setEnabled(enabled)
            b.clicked.connect(slot)
            row.addWidget(b)
        return row

    def show_at(self, pos) -> None:
        self.rebuild(self.app._devices)
        self.win.adjustSize()
        geo = self.win.frameGeometry()
        geo.moveTopLeft(pos)
        screen = self.app.app.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(max(geo.x(), avail.x()), avail.right() - geo.width())
            y = min(max(geo.y() - geo.height(), avail.y()), avail.bottom() - geo.height())
            self.win.move(x, y)
        else:
            self.win.move(pos)
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()


def _make_panel_filter(panel: "TrayPanel"):
    from PySide6.QtCore import QEvent, QObject, QTimer

    class PanelFilter(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            # Only close when focus truly leaves the panel (not when clicking its buttons).
            if obj is panel.win and event.type() == QEvent.Type.WindowDeactivate:
                QTimer.singleShot(0, panel.hide_if_focus_outside)
            return False

    return PanelFilter(panel.win)


class TrayApp:
    def __init__(self) -> None:
        _require_qt()
        from PySide6.QtCore import QObject, QTimer, Signal
        from PySide6.QtGui import QAction, QCursor
        from PySide6.QtWidgets import (
            QApplication,
            QMenu,
            QMessageBox,
            QSystemTrayIcon,
            QWidget,
        )

        self._QAction = QAction
        self._QMenu = QMenu
        self._QMessageBox = QMessageBox
        self._QSystemTrayIcon = QSystemTrayIcon
        self._QCursor = QCursor

        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("bt-resolver")
        self.app.setApplicationVersion(__version__)
        # Do not setDesktopFileName — under some snaps/portals it errors with
        # "Can't manually register a io.snapcraft application".

        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise SystemExit("No system tray available on this desktop session.")

        self._devices: list[DeviceSnapshot] = []
        self._jobs: queue.Queue[tuple[str, Callable[[], Any]] | None] = queue.Queue()
        self._menu_dirty = True
        self._tool_subs: dict[str, Any] = {}
        self._msg_parent = QWidget()
        self._msg_parent.hide()

        class Bridge(QObject):
            finished = Signal(str, object)
            status = Signal(str)

        self._bridge = Bridge()
        self._bridge.finished.connect(self._on_job_finished)
        self._bridge.status.connect(self._on_status)

        self._worker = threading.Thread(target=self._worker_loop, name="bt-resolver-tray", daemon=True)
        self._worker.start()

        self.tray = QSystemTrayIcon(make_tray_icon(), self.app)
        self.tray.setToolTip(f"bt-resolver {__version__}\nLeft-click: panel · Right-click: menu")
        # Right-click: Plasma StatusNotifier native menu only (no manual QMenu.popup).
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._ensure_menu_fresh)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)

        self.panel = TrayPanel(self)

        self._refresh_timer = QTimer()
        self._refresh_timer.setInterval(8000)
        self._refresh_timer.timeout.connect(self.refresh_devices)
        self._refresh_timer.start()

        self.tray.show()
        self.refresh_devices()

    def _worker_loop(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                break
            op_id, fn = item
            self._bridge.status.emit(op_id)
            try:
                result: object = fn()
            except (KeyboardInterrupt, SystemExit) as exc:
                result = exc
                self._bridge.finished.emit(op_id, result)
                break
            except Exception as exc:  # noqa: BLE001
                result = exc
            self._bridge.finished.emit(op_id, result)

    def _submit(self, op_id: str, fn: Callable[[], Any]) -> None:
        self.tray.setToolTip(f"bt-resolver — {op_id}…")
        self._jobs.put((op_id, fn))

    def _on_status(self, op_id: str) -> None:
        self.tray.setToolTip(f"bt-resolver — {op_id}…")

    def _on_job_finished(self, op_id: str, result: object) -> None:
        try:
            if isinstance(result, BaseException):
                self._notify_error(op_id, result)
            elif op_id == "refresh":
                assert isinstance(result, list)
                self._devices = result
                any_conn = any(d.connected for d in self._devices)
                self.tray.setIcon(make_tray_icon(connected=any_conn))
                self._menu_dirty = True
                self._set_tool_status(
                    "refresh",
                    f"Updated · {len(result)} device(s) — reopen menu to reload list",
                )
                if "refresh" in self._tool_subs:
                    self._tool_subs["refresh"]["busy"] = False
                if not self.menu.isVisible():
                    self._rebuild_menu()
                    self._menu_dirty = False
                if self.panel.is_visible():
                    self.panel.rebuild(self._devices)
            elif op_id.startswith(("connect:", "resolve:", "disconnect:", "pair:", "forget:")):
                self._notify_ok(op_id, str(result))
                self.refresh_devices()
            elif op_id == "scan":
                if "scan" in self._tool_subs:
                    self._tool_subs["scan"]["busy"] = False
                self._fill_tool_submenu("scan", str(result))
                self._notify_ok(op_id, str(result).splitlines()[0] if str(result) else "Scan done")
                self.refresh_devices()
            elif op_id == "doctor":
                if "doctor" in self._tool_subs:
                    self._tool_subs["doctor"]["busy"] = False
                self._fill_tool_submenu("doctor", str(result))
                self._show_text(op_id, str(result))
            elif op_id.startswith("diagnose:"):
                self._show_text(op_id, str(result))
            else:
                self._notify_ok(op_id, str(result))
        finally:
            if isinstance(result, BaseException):
                for key in ("refresh", "scan", "doctor"):
                    if key in self._tool_subs:
                        self._tool_subs[key]["busy"] = False
            self.tray.setToolTip(
                f"bt-resolver {__version__}\nLeft-click: panel · Right-click: menu"
            )

    def _ensure_menu_fresh(self) -> None:
        if self._menu_dirty:
            self._rebuild_menu()
            self._menu_dirty = False

    def _notify_ok(self, title: str, body: str) -> None:
        self.tray.showMessage(
            title,
            body[:500],
            self._QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def _notify_error(self, title: str, exc: BaseException) -> None:
        if isinstance(exc, BtResolverError):
            msg = f"[{exc.reason}] {exc}"
        else:
            msg = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        self.tray.showMessage(
            title,
            msg[:500],
            self._QSystemTrayIcon.MessageIcon.Critical,
            8000,
        )

    def _show_text(self, title: str, text: str) -> None:
        if len(text) < 280:
            self._notify_ok(title, text)
            return
        box = self._QMessageBox(self._msg_parent)
        box.setWindowTitle(title)
        box.setText(title)
        box.setDetailedText(text)
        box.setIcon(self._QMessageBox.Icon.Information)
        box.setStandardButtons(self._QMessageBox.StandardButton.Ok)
        box.show()

    def _on_activated(self, reason) -> None:
        # Right-click is Plasma's native StatusNotifier context menu — do not popup().
        if reason not in (
            self._QSystemTrayIcon.ActivationReason.Trigger,
            self._QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            return
        if self.panel.is_visible():
            self.panel.hide()
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._show_panel)

    def _show_panel(self) -> None:
        geo = self.tray.geometry()
        if geo.isValid() and geo.width() > 0 and geo.height() > 0:
            pos = geo.bottomLeft()
        else:
            pos = self._QCursor.pos()
        self.panel.show_at(pos)

    def _rebuild_menu(self) -> None:
        """
        Plasma dismisses the menu on normal actions. Submenus (▸) stay open —
        Refresh/Scan/Doctor use that pattern (no auto-reopen / no blinking).
        """
        self.menu.clear()
        self._tool_subs = {}

        header = self._QAction(f"bt-resolver {__version__}", self.menu)
        header.setEnabled(False)
        self.menu.addAction(header)
        self.menu.addSeparator()

        self._add_tool_submenu(
            "refresh",
            "Refresh devices",
            "Open to refresh…",
            self._run_refresh_from_menu,
        )
        self._add_tool_submenu(
            "scan",
            "Scan (10s)",
            "Open to scan…",
            self._run_scan_from_menu,
        )
        self._add_tool_submenu(
            "doctor",
            "Doctor",
            "Open to run doctor…",
            self._run_doctor_from_menu,
        )
        self.menu.addSeparator()

        if not self._devices:
            empty = self._QAction("No devices yet", self.menu)
            empty.setEnabled(False)
            self.menu.addAction(empty)
        else:
            for snap in self._devices:
                mark = "●" if snap.connected else ("◇" if not snap.paired else "○")
                unpaired = " unpaired" if not snap.paired else ""
                label = f"{mark} {snap.name}  [{snap.profile}]{unpaired}"
                sub = self._QMenu(label, self.menu)
                if not snap.paired:
                    act_pair = self._QAction("Pair + trust", sub)
                    act_pair.triggered.connect(
                        lambda _=False, a=snap.address, n=snap.name: self.pair_device(a, n)
                    )
                    sub.addAction(act_pair)
                act_resolve = self._QAction("Resolve (low-level)", sub)
                act_resolve.triggered.connect(
                    lambda _=False, a=snap.address, n=snap.name: self.resolve_device(a, n)
                )
                sub.addAction(act_resolve)
                act_conn = self._QAction("Connect", sub)
                act_conn.triggered.connect(
                    lambda _=False, a=snap.address, n=snap.name: self.connect_device(a, n)
                )
                sub.addAction(act_conn)
                act_disc = self._QAction("Disconnect", sub)
                act_disc.setEnabled(snap.connected)
                act_disc.triggered.connect(
                    lambda _=False, a=snap.address, n=snap.name: self.disconnect_device(a, n)
                )
                sub.addAction(act_disc)
                act_diag = self._QAction("Diagnose", sub)
                act_diag.triggered.connect(
                    lambda _=False, a=snap.address, n=snap.name: self.diagnose_device(a, n)
                )
                sub.addAction(act_diag)
                act_forget = self._QAction("Forget", sub)
                act_forget.triggered.connect(
                    lambda _=False, a=snap.address, n=snap.name: self.forget_device(a, n)
                )
                sub.addAction(act_forget)
                self.menu.addMenu(sub)

        self.menu.addSeparator()
        quit_act = self._QAction("Quit", self.menu)
        quit_act.triggered.connect(self.quit)
        self.menu.addAction(quit_act)

    def _add_tool_submenu(
        self,
        key: str,
        title: str,
        idle_text: str,
        on_show: Callable[[], None],
    ) -> None:
        sub = self._QMenu(title, self.menu)
        status = self._QAction(idle_text, sub)
        status.setEnabled(False)
        sub.addAction(status)
        sub.aboutToShow.connect(on_show)
        self.menu.addMenu(sub)
        self._tool_subs[key] = {"menu": sub, "status": status, "idle": idle_text, "busy": False}

    def _set_tool_status(self, key: str, text: str) -> None:
        entry = self._tool_subs.get(key)
        if not entry:
            return
        entry["status"].setText(text)

    def _fill_tool_submenu(self, key: str, text: str) -> None:
        entry = self._tool_subs.get(key)
        if not entry:
            return
        sub = entry["menu"]
        sub.clear()
        lines = [ln for ln in text.splitlines() if ln.strip()] or ["(no output)"]
        for ln in lines[:30]:
            act = self._QAction(ln[:120], sub)
            act.setEnabled(False)
            sub.addAction(act)
        entry["status"] = sub.actions()[0] if sub.actions() else entry["status"]

    def _run_refresh_from_menu(self) -> None:
        entry = self._tool_subs.get("refresh")
        if entry and entry.get("busy"):
            return
        if entry is not None:
            entry["busy"] = True
        self._set_tool_status("refresh", "Refreshing…")
        self.refresh_devices()

    def _run_scan_from_menu(self) -> None:
        entry = self._tool_subs.get("scan")
        if entry and entry.get("busy"):
            return
        if entry is not None:
            entry["busy"] = True
        self._set_tool_status("scan", "Scanning…")
        self.scan_devices()

    def _run_doctor_from_menu(self) -> None:
        entry = self._tool_subs.get("doctor")
        if entry and entry.get("busy"):
            return
        if entry is not None:
            entry["busy"] = True
        self._set_tool_status("doctor", "Running doctor…")
        self.run_doctor()

    def refresh_devices(self) -> None:
        def job() -> list[DeviceSnapshot]:
            async def _inner() -> list[DeviceSnapshot]:
                async with open_manager() as mgr:
                    devices = await mgr.list_devices()
                    interesting = [
                        d
                        for d in devices
                        if d.paired
                        or d.bonded
                        or d.connected
                        or d.icon == "input-gaming"
                        or detect_profile(d).name in {"xbox", "dualshock"}
                    ]
                    src = interesting or devices
                    return [DeviceSnapshot.from_device(d) for d in src]

            return _run_async(_inner())

        self._submit("refresh", job)

    def scan_devices(self) -> None:
        def job() -> str:
            async def _inner() -> str:
                async with open_manager() as mgr:
                    devices = await mgr.scan(timeout=10.0)
                    lines = [
                        f"{d.address}  {d.display_name}  "
                        f"{'connected' if d.connected else '-'}"
                        for d in devices[:30]
                    ]
                    return f"Scan found {len(devices)} device(s)\n" + "\n".join(lines)

            return _run_async(_inner())

        self._submit("scan", job)

    def run_doctor(self) -> None:
        def job() -> str:
            async def _inner() -> str:
                async with open_manager() as mgr:
                    return (await run_doctor(mgr)).format()

            return _run_async(_inner())

        self._submit("doctor", job)

    def pair_device(self, address: str, name: str) -> None:
        def job() -> str:
            async def _inner() -> str:
                async with open_manager() as mgr:
                    d = await mgr.pair(address, timeout=60.0)
                    return f"Paired/trusted: {d.display_name} ({d.address})"

            return _run_async(_inner())

        self._submit(f"pair:{name}", job)

    def connect_device(self, address: str, name: str) -> None:
        def job() -> str:
            async def _inner() -> str:
                async with open_manager() as mgr:
                    result = await mgr.connect(address, wait_stable=4.0, verify_usable=True)
                    return (
                        f"{name}: usable via {result.profile} "
                        f"(stable {result.stability.connected_ratio:.0%})"
                    )

            return _run_async(_inner())

        self._submit(f"connect:{name}", job)

    def resolve_device(self, address: str, name: str) -> None:
        def job() -> str:
            async def _inner() -> str:
                async with open_manager() as mgr:
                    result = await resolve_connection(
                        mgr,
                        address,
                        wait_stable=3.0,
                        wake_scan=6.0,
                        usable_wait=6.0,
                    )
                    if not result.usable:
                        raise BtResolverError(
                            f"{name}: not usable ({result.reason})",
                            reason=result.reason,
                        )
                    return f"{name}: OK via {result.winning_strategy}\n{result.format()}"

            return _run_async(_inner())

        self._submit(f"resolve:{name}", job)

    def disconnect_device(self, address: str, name: str) -> None:
        def job() -> str:
            async def _inner() -> str:
                async with open_manager() as mgr:
                    d = await mgr.disconnect(address)
                    return f"Disconnected {d.display_name}"

            return _run_async(_inner())

        self._submit(f"disconnect:{name}", job)

    def forget_device(self, address: str, name: str) -> None:
        def job() -> str:
            async def _inner() -> str:
                async with open_manager() as mgr:
                    # Remove BlueZ bond; print cache purge hint for stubborn auth loops.
                    return await mgr.remove(address, purge_cache=True)

            return _run_async(_inner())

        self._submit(f"forget:{name}", job)

    def diagnose_device(self, address: str, name: str) -> None:
        def job() -> str:
            async def _inner() -> str:
                async with open_manager() as mgr:
                    return (await run_diagnose(mgr, address, sample_seconds=3.0)).format()

            return _run_async(_inner())

        self._submit(f"diagnose:{name}", job)

    def quit(self) -> None:
        self._refresh_timer.stop()
        self._jobs.put(None)
        self._worker.join(timeout=2.0)
        self.panel.hide()
        self.tray.hide()
        self.app.quit()

    def run(self) -> int:
        import signal

        def _sigint(*_args: object) -> None:
            self.quit()

        signal.signal(signal.SIGINT, _sigint)
        # Deliver SIGINT to Python even while Qt blocks in C++.
        from PySide6.QtCore import QTimer

        timer = QTimer()
        timer.start(200)
        timer.timeout.connect(lambda: None)
        return self.app.exec()


def main(argv: list[str] | None = None) -> int:
    _ = argv
    _require_qt()
    try:
        return TrayApp().run()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
