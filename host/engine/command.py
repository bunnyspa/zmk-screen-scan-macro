"""Command dataclass + CommandSink implementations.

Targets host/protocol.py's real wire-format encoder directly - the Phase-1
plan's stub sink is no longer needed since the real transport now exists.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol as wire  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class Command:
    action: int
    modifiers: int = 0
    keycodes: tuple[int, ...] = field(default_factory=tuple)
    mouse_buttons: int = 0
    dx: int = 0
    dy: int = 0

    def encode(self, seq: int) -> bytes:
        return wire.encode_command(
            self.action,
            seq,
            modifiers=self.modifiers,
            keycodes=self.keycodes,
            mouse_buttons=self.mouse_buttons,
            dx=self.dx,
            dy=self.dy,
        )


class CommandSink(Protocol):
    def send(self, command: Command) -> None: ...


class HidCommandSink:
    """Sends Command objects over an already-open Raw HID device handle."""

    def __init__(self, dev):
        self._dev = dev
        self._seq = 0

    def send(self, command: Command) -> None:
        self._seq = (self._seq + 1) % 256
        payload = command.encode(self._seq)
        report = bytes([0x00]) + payload
        # dev.write()'s return value (bytes actually written, per hidapi)
        # was never checked or logged - a report that looks fully "sent"
        # from the caller's side (no exception - engine/runner.py's own
        # "sending key press" log fires either way) but never actually
        # reaches the device would previously have been indistinguishable
        # from one that did.
        written = self._dev.write(report)
        logger.info(
            "HidCommandSink: wrote %s/%d bytes (action=0x%02x seq=%d)",
            written, len(report), command.action, self._seq,
        )


class RecordingCommandSink:
    """No-op sink that records every Command it receives - for tests/dry runs."""

    def __init__(self):
        self.sent: list[Command] = []

    def send(self, command: Command) -> None:
        self.sent.append(command)
