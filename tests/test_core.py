# test_core.py
# SPDX-License-Identifier: MIT
#
# Python port of CycFlow/Tests/test_Core.cpp

import pytest
import cycflow


# ---------------------------------------------------------------------------
# PReg
# ---------------------------------------------------------------------------

class TestPReg:
    def test_unique_ids(self):
        id1 = cycflow.PReg.get_id("ParamA")
        id2 = cycflow.PReg.get_id("ParamB")
        id1_again = cycflow.PReg.get_id("ParamA")

        assert id1 != id2
        assert id1 == id1_again
        assert cycflow.PReg.get_name(id1) == "ParamA"


# ---------------------------------------------------------------------------
# RecRule
# ---------------------------------------------------------------------------

HEADER_SIZE = 8  # TimeStamp is a double = 8 bytes

class TestRecRule:
    def test_offset_calculation(self):
        attrs = [
            cycflow.PAttr("IntVal", cycflow.DataType.Int32, 1),
            cycflow.PAttr("DblVal", cycflow.DataType.Double, 1),
            cycflow.PAttr("StrVal", cycflow.DataType.Char,  10),
        ]
        rule = cycflow.RecRule(attrs)

        # Total size: header + 4 + 8 + 10
        assert rule.get_rec_size() == HEADER_SIZE + 4 + 8 + 10

        id_int = cycflow.PReg.get_id("IntVal")
        id_dbl = cycflow.PReg.get_id("DblVal")
        id_str = cycflow.PReg.get_id("StrVal")

        assert rule.get_offset_by_id(id_int) == HEADER_SIZE + 0
        assert rule.get_offset_by_id(id_dbl) == HEADER_SIZE + 4
        assert rule.get_offset_by_id(id_str) == HEADER_SIZE + 12

    def test_aligned_layout(self):
        """When align=True, fields should be sorted by decreasing element size
        and the total record size should be padded to a multiple of the
        largest element size."""
        attrs = [
            cycflow.PAttr("SmallVal", cycflow.DataType.Int8),
            cycflow.PAttr("BigVal",   cycflow.DataType.Double),
            cycflow.PAttr("MedVal",   cycflow.DataType.Int32),
        ]
        rule = cycflow.RecRule(attrs, align=True)

        id_big   = cycflow.PReg.get_id("BigVal")
        id_med   = cycflow.PReg.get_id("MedVal")
        id_small = cycflow.PReg.get_id("SmallVal")

        # In aligned mode, Double (8) comes first, then Int32 (4), then Int8 (1).
        # Header is Double (8 bytes).
        # Layout: [Header:8][BigVal:8][MedVal:4][SmallVal:1] + padding to 8-byte boundary
        assert rule.get_offset_by_id(id_big) == HEADER_SIZE
        assert rule.get_offset_by_id(id_med) == HEADER_SIZE + 8
        assert rule.get_offset_by_id(id_small) == HEADER_SIZE + 12

        # Record size should be a multiple of the largest element size (8)
        assert rule.get_rec_size() % 8 == 0

    def test_make_rule_align(self):
        """make_rule() helper should pass align to RecRule."""
        rule = cycflow.make_rule(
            [("X", cycflow.DataType.Int8), ("Y", cycflow.DataType.Double)],
            align=True,
        )
        # Aligned: Double first (size 8), then Int8 (size 1) + padding
        assert rule.get_rec_size() % 8 == 0


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------

class TestRecord:
    def test_data_access(self):
        id_int = cycflow.PReg.get_id("TestInt")
        id_dbl = cycflow.PReg.get_id("TestDbl")

        attrs = [
            cycflow.PAttr("TestInt", cycflow.DataType.Int32),
            cycflow.PAttr("TestDbl", cycflow.DataType.Double),
        ]
        rule = cycflow.RecRule(attrs)
        buffer = cycflow.RecBuffer(rule, capacity=10)

        writer = cycflow.RecordWriter(buffer, batch_capacity=10)
        rec = writer.next_record()
        rec.set_int32(id_int, 42)
        rec.set_double(id_dbl, 3.14159)
        writer.commit_record()
        writer.flush()

        reader = cycflow.RecordReader(buffer, batch_capacity=10)
        rec_read = reader.next_record()
        assert rec_read.get_int32(id_int) == 42
        assert rec_read.get_double(id_dbl) == pytest.approx(3.14159)


# ---------------------------------------------------------------------------
# RecBuffer  (mirrors CircularBufferTest + RecBufferTest)
# ---------------------------------------------------------------------------

class TestRecBuffer:
    def _make_buffer(self, capacity=10):
        attrs = [cycflow.PAttr("Val", cycflow.DataType.Int32)]
        rule = cycflow.RecRule(attrs)
        return cycflow.RecBuffer(rule, capacity=capacity), rule

    def test_initially_empty(self):
        buf, _ = self._make_buffer()
        assert buf.size() == 0

    def test_push_and_size(self):
        buf, rule = self._make_buffer(capacity=3)
        writer = cycflow.RecordWriter(buf, batch_capacity=3, block_on_full=False)
        id_val = cycflow.PReg.get_id("Val")

        for v in (1, 2, 3):
            rec = writer.next_record()
            rec.set_int32(id_val, v)
            writer.commit_record()
        writer.flush()

        assert buf.size() == 3

    def test_overwrite_behavior(self):
        """Pushing to a full circular buffer overwrites oldest data."""
        buf, rule = self._make_buffer(capacity=3)
        writer = cycflow.RecordWriter(buf, batch_capacity=4, block_on_full=False)
        id_val = cycflow.PReg.get_id("Val")

        for v in (1, 2, 3, 4):
            rec = writer.next_record()
            rec.set_int32(id_val, v)
            writer.commit_record()
        writer.flush()

        assert buf.size() == 3

    def test_write_and_read_relative(self):
        attrs = [cycflow.PAttr("Val", cycflow.DataType.Int32)]
        rule = cycflow.RecRule(attrs)
        buf = cycflow.RecBuffer(rule, capacity=10)
        writer = cycflow.RecordWriter(buf, batch_capacity=5)
        id_val = cycflow.PReg.get_id("Val")

        for i in range(5):
            rec = writer.next_record()
            rec.set_int32(id_val, (i + 1) * 10)
            writer.commit_record()
        writer.flush()

        assert buf.size() == 5

        # readRelative(1, ...) → 2nd record → value 20
        snap = buf.snapshot()
        assert snap["Val"][1] == 20


# ---------------------------------------------------------------------------
# AsyncIntegrationTest  (parametrized like the C++ INSTANTIATE_TEST_SUITE_P)
#
# NOTE: RecordWriter.next_record() / commit_record() hold the GIL without
# releasing it. A full buffer causes next_record() to block in C++ while
# holding the GIL → deadlock with any Python reader waiting to reacquire it.
# Fix: buffer capacity >= TOTAL (writer never blocks) + read via next_record()
# exactly TOTAL times, mirroring the C++ test. next_record() has
# py::call_guard<py::gil_scoped_release> so it never deadlocks.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("p1,p2", [
    (0.2, 0.2),
    (0.2, 1.0),
    (1.0, 0.2),
    (1.0, 1.0),
])
def test_write_read_flow(p1, p2):
    id_val = cycflow.PReg.get_id("Val")
    attrs = [cycflow.PAttr("Val", cycflow.DataType.Int32)]
    rule = cycflow.RecRule(attrs)

    TOTAL = 10_000
    batch_w = max(int(1000 * p1), 1)
    batch_r = max(int(1000 * p2), 1)

    buf = cycflow.RecBuffer(rule, capacity=TOTAL + 1000)
    writer = cycflow.RecordWriter(buf, batch_capacity=batch_w)
    reader = cycflow.RecordReader(buf, batch_capacity=batch_r)

    for i in range(TOTAL):
        rec = writer.next_record()
        rec.set_int32(id_val, i)
        writer.commit_record()
    writer.flush()

    # Read exactly TOTAL records — mirrors the C++ loop.
    # next_record() releases the GIL while waiting, so no deadlock.
    received = []
    for _ in range(TOTAL):
        rec = reader.next_record()
        if not rec.is_valid():
            break
        received.append(rec.get_int32(id_val))
    reader.stop()

    assert len(received) == TOTAL
    for i, v in enumerate(received):
        assert v == i, f"Mismatch at index {i}: got {v}"
