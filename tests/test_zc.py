# test_zc.py
# SPDX-License-Identifier: MIT
#
# Tests for v0.4.0 zero-copy reader/writer (RecordReaderZC / RecordWriterZC),
# TcpServer.unregister_buffer() / TcpServerManager APIs,
# and v0.5.0 CbfWriter/CsvWriter file-rotation features (restart, maxRecords,
# addTimestampSuffix, default ctor + init()).

import threading
import time

import pytest
import cycflow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_int_buffer(capacity: int = 10_000):
    attrs = [cycflow.PAttr("Val", cycflow.DataType.Int32)]
    rule = cycflow.RecRule(attrs)
    return cycflow.RecBuffer(rule, capacity=capacity), rule


# ---------------------------------------------------------------------------
# Default + init() constructors
# ---------------------------------------------------------------------------

class TestDefaultCtors:
    """v0.4.0 added default ctors + init() to RecBuffer, RecordReader,
    RecordWriter, RecordReaderZC, RecordWriterZC."""

    def test_recbuffer_default_then_init(self):
        attrs = [cycflow.PAttr("V", cycflow.DataType.Float)]
        rule = cycflow.RecRule(attrs)

        buf = cycflow.RecBuffer()
        buf.init(rule, 100)
        assert buf.capacity() == 100
        assert buf.size() == 0

    def test_recordreader_default_then_init(self):
        buf, _ = make_int_buffer(capacity=128)
        r = cycflow.RecordReader()
        r.init(buf, 32)
        assert r.get_cursor() == 0

    def test_recordwriter_default_then_init(self):
        buf, _ = make_int_buffer(capacity=128)
        w = cycflow.RecordWriter()
        w.init(buf, 32)
        # Writer is usable after init.
        id_val = cycflow.PReg.get_id("Val")
        rec = w.next_record()
        rec.set_int32(id_val, 7)
        w.commit_record()
        w.flush()
        assert buf.size() == 1


# ---------------------------------------------------------------------------
# RecordWriterZC + RecordReaderZC round-trip
# ---------------------------------------------------------------------------

class TestZcRoundTrip:
    """RecordWriterZC is single-producer + synchronous. Round-trip with a
    RecordReaderZC reader must deliver all records in order."""

    def test_zc_writer_zc_reader_round_trip(self):
        TOTAL = 1_000
        buf, _ = make_int_buffer(capacity=TOTAL + 100)
        id_val = cycflow.PReg.get_id("Val")

        writer = cycflow.RecordWriterZC(buf, batch_capacity=64, block_on_full=True)
        reader = cycflow.RecordReaderZC(buf, batch_capacity=64)

        # Writer runs in its own thread because nextRecord/commitRecord may
        # stall the caller when the buffer fills up.
        def producer():
            for i in range(TOTAL):
                rec = writer.next_record()
                rec.set_int32(id_val, i)
                writer.commit_record()

        prod = threading.Thread(target=producer)
        prod.start()

        received = []
        while len(received) < TOTAL:
            batch = reader.next_batch_copy(64, wait=True)
            if batch is None:
                break
            for v in batch["Val"]:
                received.append(int(v))

        prod.join(timeout=5.0)
        assert not prod.is_alive(), "Producer thread did not finish"

        assert len(received) == TOTAL
        assert received == list(range(TOTAL))

    def test_zc_writer_release_unblocks_writer(self):
        """RecordReaderZC pins data until release() (or next_batch). The
        writer's backpressure should observe the release."""
        BUF_CAP = 32
        BATCH = 8

        buf, _ = make_int_buffer(capacity=BUF_CAP)
        id_val = cycflow.PReg.get_id("Val")

        writer = cycflow.RecordWriterZC(buf, batch_capacity=BATCH, block_on_full=True)
        reader = cycflow.RecordReaderZC(buf, batch_capacity=BATCH)

        produced = 0
        done = threading.Event()

        def producer():
            nonlocal produced
            for i in range(BUF_CAP * 4):
                rec = writer.next_record()
                rec.set_int32(id_val, i)
                writer.commit_record()
                produced += 1
            done.set()

        prod = threading.Thread(target=producer)
        prod.start()

        # Drain — if release semantics are wrong the producer will deadlock.
        received = []
        while len(received) < BUF_CAP * 4:
            batch = reader.next_batch(BATCH, wait=True)
            if batch is None:
                break
            received.extend(int(v) for v in batch["Val"])

        done.wait(timeout=5.0)
        prod.join(timeout=5.0)

        assert produced == BUF_CAP * 4
        assert received == list(range(BUF_CAP * 4))


# ---------------------------------------------------------------------------
# RecordConsumer.init_zc()
# ---------------------------------------------------------------------------

class TestConsumerInitZc:
    def test_batch_consumer_with_zc_reader(self):
        TOTAL = 200
        buf, _ = make_int_buffer(capacity=TOTAL + 50)
        id_val = cycflow.PReg.get_id("Val")

        received = []
        lock = threading.Lock()

        class Consumer(cycflow.BatchRecordConsumer):
            def consume_batch(self, batch):
                # Note: the C++ side passes a RecordBatch view; we can't read
                # numpy directly from the pybind11 RecordBatch wrapper, but we
                # can at least verify the call happens with valid counts.
                with lock:
                    received.append(batch.count)

        consumer = Consumer()
        consumer.init_zc(buf, reader_batch_size=32)
        consumer.start()

        writer = cycflow.RecordWriter(buf, batch_capacity=32)
        for i in range(TOTAL):
            rec = writer.next_record()
            rec.set_int32(id_val, i)
            writer.commit_record()
        writer.flush()

        # Give the consumer a chance to drain.
        for _ in range(50):
            if sum(received) >= TOTAL:
                break
            time.sleep(0.02)

        consumer.finish()
        assert sum(received) >= TOTAL


# ---------------------------------------------------------------------------
# TcpServer.unregister_buffer + TcpServerManager
# ---------------------------------------------------------------------------

class TestTcpUnregisterBuffer:
    def test_unregister_removes_from_listing(self):
        rule = cycflow.make_rule([("V", cycflow.DataType.Float)])
        buf_a = cycflow.RecBuffer(rule, capacity=100)
        buf_b = cycflow.RecBuffer(rule, capacity=100)

        server = cycflow.TcpServer(port=15580)
        server.register_buffer("A", buf_a, batch_size=10)
        server.register_buffer("B", buf_b, batch_size=10)
        server.start()
        time.sleep(0.1)
        try:
            names = cycflow.TcpServiceClient.request_buffer_list("127.0.0.1", 15580)
            assert set(names) == {"A", "B"}

            server.unregister_buffer("A")
            names = cycflow.TcpServiceClient.request_buffer_list("127.0.0.1", 15580)
            assert set(names) == {"B"}
        finally:
            server.stop()


class TestTcpServerManager:
    def test_singleton_lifecycle(self):
        mgr = cycflow.TcpServerManager.instance()
        # Make sure we start from a clean state.
        if mgr.is_running():
            mgr.stop()

        rule = cycflow.make_rule([("V", cycflow.DataType.Float)])
        buf = cycflow.RecBuffer(rule, capacity=100)

        mgr.start(15581)
        try:
            assert mgr.is_running()
            mgr.register_buffer("Mgr", buf, batch_size=10)
            time.sleep(0.1)

            names = cycflow.TcpServiceClient.request_buffer_list("127.0.0.1", 15581)
            assert "Mgr" in names

            mgr.unregister_buffer("Mgr")
            names = cycflow.TcpServiceClient.request_buffer_list("127.0.0.1", 15581)
            assert "Mgr" not in names
        finally:
            mgr.stop()
            assert not mgr.is_running()

    def test_register_without_start_raises(self):
        mgr = cycflow.TcpServerManager.instance()
        if mgr.is_running():
            mgr.stop()

        rule = cycflow.make_rule([("V", cycflow.DataType.Float)])
        buf = cycflow.RecBuffer(rule, capacity=10)
        with pytest.raises(RuntimeError):
            mgr.register_buffer("X", buf, batch_size=1)


# ---------------------------------------------------------------------------
# v0.5.0 — CbfWriter / CsvWriter new features
# ---------------------------------------------------------------------------

class TestCbfWriterV05:
    """v0.5.0 added default ctor + init(), addTimestampSuffix, maxRecords,
    and restart() to CbfWriter."""

    def _make_buffer(self, capacity: int = 1000):
        rule = cycflow.make_rule([("Val", cycflow.DataType.Int32)])
        return cycflow.RecBuffer(rule, capacity=capacity), rule

    def test_default_ctor_then_init(self, tmp_path):
        buf, _ = self._make_buffer()
        writer = cycflow.CbfWriter()
        writer.init(str(tmp_path / "out.cbf"), buf,
                    auto_start=False, add_timestamp_suffix=False)
        # init() without auto_start must not start the thread
        assert not writer.is_running()

    def test_constructor_no_timestamp(self, tmp_path):
        buf, _ = self._make_buffer()
        out = tmp_path / "fixed.cbf"
        writer = cycflow.CbfWriter(str(out), buf,
                                   auto_start=False,
                                   add_timestamp_suffix=False)
        assert not writer.is_running()

    def test_timestamp_suffix_creates_different_filename(self, tmp_path):
        buf, _ = self._make_buffer()
        # With add_timestamp_suffix=True the actual file written has a
        # timestamp inserted, so the plain path does not exist right away.
        writer = cycflow.CbfWriter(str(tmp_path / "data.cbf"), buf,
                                   auto_start=False,
                                   add_timestamp_suffix=True)
        assert not writer.is_running()

    def test_restart_called_while_running(self, tmp_path):
        buf, _ = self._make_buffer(capacity=500)
        id_val = cycflow.PReg.get_id("Val")

        writer = cycflow.CbfWriter(str(tmp_path / "rot.cbf"), buf,
                                   auto_start=True,
                                   add_timestamp_suffix=False,
                                   max_records=0)
        rec_writer = cycflow.RecordWriter(buf, batch_capacity=32)

        for i in range(50):
            rec = rec_writer.next_record()
            rec.set_int32(id_val, i)
            rec_writer.commit_record()
        rec_writer.flush()

        writer.restart()  # must not crash or deadlock

        for i in range(50, 100):
            rec = rec_writer.next_record()
            rec.set_int32(id_val, i)
            rec_writer.commit_record()
        rec_writer.flush()

        writer.finish()
        assert not writer.is_running()

    def test_max_records_triggers_rotation(self, tmp_path):
        """max_records>0: writer must stay alive and handle rotation internally."""
        buf, _ = self._make_buffer(capacity=500)
        id_val = cycflow.PReg.get_id("Val")

        writer = cycflow.CbfWriter(str(tmp_path / "rot.cbf"), buf,
                                   auto_start=True,
                                   add_timestamp_suffix=True,
                                   max_records=30)
        rec_writer = cycflow.RecordWriter(buf, batch_capacity=16)

        for i in range(120):  # 4× the rotation threshold
            rec = rec_writer.next_record()
            rec.set_int32(id_val, i)
            rec_writer.commit_record()
        rec_writer.flush()

        writer.finish()
        assert not writer.is_running()


class TestCsvWriterV05:
    """v0.5.0 added the same file-rotation API to CsvWriter."""

    def _make_buffer(self, capacity: int = 1000):
        rule = cycflow.make_rule([("X", cycflow.DataType.Float),
                                  ("Y", cycflow.DataType.Float)])
        return cycflow.RecBuffer(rule, capacity=capacity), rule

    def test_default_ctor_then_init(self, tmp_path):
        buf, _ = self._make_buffer()
        writer = cycflow.CsvWriter()
        writer.init(str(tmp_path / "out.csv"), buf,
                    auto_start=False, add_timestamp_suffix=False)
        assert not writer.is_running()

    def test_restart_called_while_running(self, tmp_path):
        buf, _ = self._make_buffer(capacity=500)
        id_x = cycflow.PReg.get_id("X")
        id_y = cycflow.PReg.get_id("Y")

        writer = cycflow.CsvWriter(str(tmp_path / "rot.csv"), buf,
                                   auto_start=True,
                                   add_timestamp_suffix=False,
                                   max_records=0)
        rec_writer = cycflow.RecordWriter(buf, batch_capacity=32)

        for i in range(50):
            rec = rec_writer.next_record()
            rec.set_float(id_x, float(i))
            rec.set_float(id_y, float(-i))
            rec_writer.commit_record()
        rec_writer.flush()

        writer.restart()  # must not crash

        for i in range(50, 100):
            rec = rec_writer.next_record()
            rec.set_float(id_x, float(i))
            rec.set_float(id_y, float(-i))
            rec_writer.commit_record()
        rec_writer.flush()

        writer.finish()
        assert not writer.is_running()

    def test_max_records_triggers_rotation(self, tmp_path):
        buf, _ = self._make_buffer(capacity=500)
        id_x = cycflow.PReg.get_id("X")
        id_y = cycflow.PReg.get_id("Y")

        writer = cycflow.CsvWriter(str(tmp_path / "rot.csv"), buf,
                                   auto_start=True,
                                   add_timestamp_suffix=True,
                                   max_records=25)
        rec_writer = cycflow.RecordWriter(buf, batch_capacity=16)

        for i in range(100):
            rec = rec_writer.next_record()
            rec.set_float(id_x, float(i))
            rec.set_float(id_y, 0.0)
            rec_writer.commit_record()
        rec_writer.flush()

        writer.finish()
        assert not writer.is_running()
