"""Command dataclass + CommandSink implementations.

Targets host/protocol.py's real wire-format encoder directly - the Phase-1
plan's stub sink is no longer needed since the real transport now exists.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
import protocol as wire  # noqa: E402


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
        self._dev.write(bytes([0x00]) + payload)


class RecordingCommandSink:
    """No-op sink that records every Command it receives - for tests/dry runs."""

    def __init__(self):
        self.sent: list[Command] = []

    def send(self, command: Command) -> None:
        self.sent.append(command)
