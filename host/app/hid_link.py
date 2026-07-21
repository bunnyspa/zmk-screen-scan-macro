"""Owns the single open Raw HID connection shared by the whole app.

Reads (&ssm_tog trigger detection) run on a background thread; writes
(action commands from the engine) happen synchronously from whatever
thread calls .write() - MacroRunner's own background thread, via
engine.command.HidCommandSink. Both share one open device handle - the app
is one process, so there's no need for the multiple-processes-open-the-
same-path pattern the standalone Phase 0/1 scripts used.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

_USAGE_PAGE = 0xFF60
_USAGE_ID = 0x61
_TOG_MARKER = 0x4E


def _load_hid():
    if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
        if getattr(sys, 'frozen', False):
            dll_dir = str(Path(sys.executable).parent)
        else:
            dll_dir = str(Path(__file__).resolve().parent.parent)
        os.add_dll_directory(dll_dir)
    import hid
    return hid


def find_device():
    """Returns an opened hid.Device for the Raw HID interface, or None if
    no matching device is currently connected."""
    hid = _load_hid()
    for info in hid.enumerate():
        if info.get('usage_page') == _USAGE_PAGE and info.get('usage') == _USAGE_ID:
            return hid.Device(path=info['path'])
    return None


class HidLink(QObject):
    """toggle_received fires whenever a &ssm_tog packet (marker 0x4E)
    arrives, marshalled onto the GUI thread automatically by Qt's queued
    connection (the read loop runs on a plain Python thread, not a QThread,
    but that's fine - Qt only cares about the receiving QObject's thread
    affinity, not the emitting thread)."""

    toggle_received = pyqtSignal()
    connection_lost = pyqtSignal()

    def __init__(self, dev, parent=None):
        super(HidLink, self).__init__(parent)
        self._dev = dev
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def write(self, data: bytes) -> None:
        """Passthrough so this object can be used directly as
        engine.command.HidCommandSink's `dev`."""
        self._dev.write(data)

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                data = self._dev.read(32, timeout=500)
            except OSError:
                self.connection_lost.emit()
                return
            if not data:
                continue
            if data[0] == _TOG_MARKER:
                self.toggle_received.emit()
