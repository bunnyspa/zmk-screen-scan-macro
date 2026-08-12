"""Tests for shared/command.py's HidCommandSink - specifically that the
real dev.write() call's return value (bytes actually written, per hidapi)
is surfaced via logging rather than silently discarded, since a write that
looks fully "sent" from the caller's side but never actually reaches the
device was previously indistinguishable from one that did (see a real
report this was added to help diagnose)."""
import logging

from command import Command, HidCommandSink
import protocol as wire


class FakeDevice:
    def __init__(self, write_return):
        self.write_return = write_return
        self.writes = []

    def write(self, data):
        self.writes.append(data)
        return self.write_return


def test_send_writes_report_id_byte_plus_encoded_payload():
    dev = FakeDevice(write_return=32)
    sink = HidCommandSink(dev)

    sink.send(Command(action=wire.ACTION_KEY_PRESS, keycodes=(wire.keycode_for_letter('p'),)))

    assert len(dev.writes) == 1
    report = dev.writes[0]
    assert report[0] == 0x00  # report-id byte
    assert len(report) == 1 + wire.PACKET_SIZE


def test_send_logs_bytes_actually_written(caplog):
    dev = FakeDevice(write_return=33)  # 1 report-id byte + 32-byte packet
    sink = HidCommandSink(dev)

    with caplog.at_level(logging.INFO, logger='command'):
        sink.send(Command(action=wire.ACTION_KEY_PRESS, keycodes=(wire.keycode_for_letter('p'),)))

    assert any('wrote 33/33 bytes' in record.message for record in caplog.records)


def test_send_logs_a_short_write_distinctly_from_a_full_one(caplog):
    # A write() that returns fewer bytes than the report's actual size (or
    # -1/None, depending on the underlying binding) means the device never
    # got the full command - this must be visible, not swallowed the same
    # way a full write is.
    dev = FakeDevice(write_return=0)
    sink = HidCommandSink(dev)

    with caplog.at_level(logging.INFO, logger='command'):
        sink.send(Command(action=wire.ACTION_KEY_PRESS, keycodes=(wire.keycode_for_letter('p'),)))

    assert any('wrote 0/33 bytes' in record.message for record in caplog.records)


def test_send_increments_sequence_number_and_includes_it_in_the_log(caplog):
    dev = FakeDevice(write_return=33)
    sink = HidCommandSink(dev)

    with caplog.at_level(logging.INFO, logger='command'):
        sink.send(Command(action=wire.ACTION_KEY_PRESS, keycodes=(wire.keycode_for_letter('p'),)))
        sink.send(Command(action=wire.ACTION_KEY_PRESS, keycodes=(wire.keycode_for_letter('p'),)))

    assert dev.writes[0][4] == 1  # seq byte in the first packet (offset 1 for report-id + 3 header bytes)
    assert dev.writes[1][4] == 2
    assert any('seq=1' in record.message for record in caplog.records)
    assert any('seq=2' in record.message for record in caplog.records)
