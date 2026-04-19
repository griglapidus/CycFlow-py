# test_cbf.py
# SPDX-License-Identifier: MIT
#
# Python port of CycFlow/Tests/test_Cbf.cpp

import os
import time
import pytest
import cycflow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rule_2fields():
    attrs = [
        cycflow.PAttr("SensorValue", cycflow.DataType.Int32, 1),
        cycflow.PAttr("Voltage",     cycflow.DataType.Double, 1),
    ]
    return cycflow.RecRule(attrs)


# ---------------------------------------------------------------------------
# CbfFile low-level tests  (mirrors CbfFileTest)
# ---------------------------------------------------------------------------

class TestCbfFile:
    @pytest.fixture(autouse=True)
    def tmp_file(self, tmp_path):
        self.path = str(tmp_path / "test_data_file.cbf")
        yield
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_write_and_read_cycle(self):
        rule = make_rule_2fields()
        attrs = rule.get_attributes()
        ts_id     = attrs[0].id   # TimeStamp
        sensor_id = attrs[1].id   # SensorValue
        volt_id   = attrs[2].id   # Voltage

        # --- Write ---
        with cycflow.CbfFile() as f:
            assert f.open(self.path, cycflow.CbfMode.Write)
            assert f.is_open()

            f.set_alias("Engine_1")
            assert f.write_header(rule)
            assert f.begin_data_section()

            buf = cycflow.RecBuffer(rule, capacity=10)
            writer = cycflow.RecordWriter(buf, batch_capacity=10)

            rec = writer.next_record()
            rec.set_value(ts_id,     123456789.0)
            rec.set_value(sensor_id, 42.0)
            rec.set_value(volt_id,   3.14)
            assert f.write_record(rec)

            rec2 = writer.next_record()
            rec2.set_value(ts_id,     123456790.0)
            rec2.set_value(sensor_id, 100.0)
            rec2.set_value(volt_id,   5.55)
            assert f.write_record(rec2)

            assert f.end_data_section()

        # --- Read ---
        with cycflow.CbfFile() as f:
            assert f.open(self.path, cycflow.CbfMode.Read)

            # Header section
            hdr = f.read_section_header()
            assert hdr is not None
            assert hdr.type == int(cycflow.CbfSectionType.Header)
            assert hdr.name == "Engine_1"

            read_rule = f.read_rule(hdr)
            assert read_rule is not None
            assert len(read_rule) == 3
            assert read_rule.get_type(sensor_id) == cycflow.DataType.Int32
            assert read_rule.get_type(volt_id)   == cycflow.DataType.Double

            # Data section
            hdr2 = f.read_section_header()
            assert hdr2 is not None
            assert hdr2.type == int(cycflow.CbfSectionType.Data)
            assert hdr2.name == "Engine_1"

            rec_size = read_rule.get_rec_size()
            assert rec_size > 0
            records_count = hdr2.body_length // rec_size
            assert records_count == 2

            read_buf = cycflow.RecBuffer(read_rule, capacity=10)
            rw = cycflow.RecordWriter(read_buf, batch_capacity=10)
            rr = rw.next_record()

            assert f.read_record(rr)
            assert rr.get_value(ts_id)     == pytest.approx(123456789.0)
            assert int(rr.get_value(sensor_id)) == 42
            assert rr.get_value(volt_id)   == pytest.approx(3.14)

            rr2 = rw.next_record()
            assert f.read_record(rr2)
            assert rr2.get_value(ts_id)     == pytest.approx(123456790.0)
            assert int(rr2.get_value(sensor_id)) == 100
            assert rr2.get_value(volt_id)   == pytest.approx(5.55)

    def test_open_invalid_file(self):
        f = cycflow.CbfFile()
        assert not f.open("non_existent_file_12345.cbf", cycflow.CbfMode.Read)


# ---------------------------------------------------------------------------
# CbfReader tests  (mirrors CbfReaderTest)
# ---------------------------------------------------------------------------

class TestCbfReader:
    @pytest.fixture(autouse=True)
    def tmp_file(self, tmp_path):
        self.path = str(tmp_path / "test_data_reader.cbf")
        yield
        if os.path.exists(self.path):
            os.remove(self.path)

    def create_test_file(self, record_count: int):
        attrs = [
            cycflow.PAttr("ValueInt", cycflow.DataType.Int32),
            cycflow.PAttr("ValueDbl", cycflow.DataType.Double),
        ]
        rule = cycflow.RecRule(attrs)
        rule_attrs = rule.get_attributes()
        id_int = rule_attrs[1].id
        id_dbl = rule_attrs[2].id

        buf = cycflow.RecBuffer(rule, capacity=max(record_count + 100, 1000))
        cbf_writer = cycflow.CbfWriter(self.path, buf, auto_start=True, batch_size=100)
        cbf_writer.set_alias("TestGen")

        writer = cycflow.RecordWriter(buf, batch_capacity=100)
        for i in range(record_count):
            rec = writer.next_record()
            rec.set_int32(id_int, i)
            rec.set_double(id_dbl, i * 1.5)
            writer.commit_record()

        writer.flush()
        time.sleep(0.05)
        cbf_writer.finish()

    def test_read_valid_file_end_to_end(self):
        record_count = 500
        self.create_test_file(record_count)

        reader = cycflow.CbfReader(self.path, 2000, True, 50)
        reader.join()

        buf = reader.get_buffer()
        assert buf is not None

        rule = buf.get_rule()
        assert len(rule) == 3
        assert buf.get_total_written() == record_count

        id_int, id_dbl = -1, -1
        for attr in rule:
            if attr.name == "ValueInt":
                id_int = attr.id
            if attr.name == "ValueDbl":
                id_dbl = attr.id

        assert id_int != -1
        assert id_dbl != -1

        snap = buf.snapshot()

        # Verify 10th record
        assert snap["ValueInt"][10] == 10
        assert snap["ValueDbl"][10] == pytest.approx(15.0)

        # Verify last record
        last = record_count - 1
        assert snap["ValueInt"][last] == last
        assert snap["ValueDbl"][last] == pytest.approx(last * 1.5)

    def test_handles_missing_file_gracefully(self):
        reader = cycflow.CbfReader("non_existent_file_123.cbf", 1000, True)
        reader.join()

        assert not reader.is_valid()
        assert reader.get_buffer() is None

    def test_read_empty_data_section(self):
        attrs = [cycflow.PAttr("Val", cycflow.DataType.Int32)]
        rule = cycflow.RecRule(attrs)
        buf = cycflow.RecBuffer(rule, capacity=100)
        writer = cycflow.CbfWriter(self.path, buf, auto_start=True, batch_size=100)
        writer.finish()

        reader = cycflow.CbfReader(self.path)
        reader.join()

        rbuf = reader.get_buffer()
        assert rbuf is not None
        assert rbuf.get_total_written() == 0


# ---------------------------------------------------------------------------
# CbfWriter integration test  (mirrors CbfWriterIntegrationTest)
# ---------------------------------------------------------------------------

class TestCbfWriterIntegration:
    @pytest.fixture(autouse=True)
    def tmp_file(self, tmp_path):
        self.path = str(tmp_path / "test_data_writer.cbf")
        yield
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_producer_consumer_cycle(self):
        attrs = [
            cycflow.PAttr("Counter",   cycflow.DataType.Int32),
            cycflow.PAttr("SineValue", cycflow.DataType.Double),
        ]
        rule = cycflow.RecRule(attrs)
        rule_attrs = rule.get_attributes()
        id_ts      = rule_attrs[0].id
        id_counter = rule_attrs[1].id
        id_sine    = rule_attrs[2].id

        TOTAL = 5000
        buf = cycflow.RecBuffer(rule, capacity=2000)
        producer = cycflow.RecordWriter(buf, batch_capacity=100)
        consumer = cycflow.CbfWriter(self.path, buf, auto_start=True, batch_size=100)
        consumer.set_alias("IntgrTest")

        for i in range(TOTAL):
            rec = producer.next_record()
            rec.set_double(id_ts, float(100000 + i))
            rec.set_int32(id_counter, i)
            rec.set_double(id_sine, i * 0.5)
            producer.commit_record()

        producer.flush()
        time.sleep(0.05)
        consumer.finish()

        assert not consumer.is_running()

        # Validate via CbfFile
        with cycflow.CbfFile() as f:
            assert f.open(self.path, cycflow.CbfMode.Read)

            hdr = f.read_section_header()
            assert hdr is not None
            assert hdr.type == int(cycflow.CbfSectionType.Header)
            assert hdr.name == "IntgrTest"

            read_rule = f.read_rule(hdr)
            assert read_rule is not None

            hdr2 = f.read_section_header()
            assert hdr2 is not None
            assert hdr2.type == int(cycflow.CbfSectionType.Data)

            expected_bytes = TOTAL * read_rule.get_rec_size()
            assert hdr2.body_length == expected_bytes

            rbuf = cycflow.RecBuffer(read_rule, capacity=TOTAL + 10)
            rw   = cycflow.RecordWriter(rbuf, batch_capacity=TOTAL + 10)

            rIdTS, rIdCnt, rIdSine = -1, -1, -1
            for a in read_rule:
                if a.name == "TimeStamp":
                    rIdTS = a.id
                if a.name == "Counter":
                    rIdCnt = a.id
                if a.name == "SineValue":
                    rIdSine = a.id

            assert rIdTS   != -1
            assert rIdCnt  != -1
            assert rIdSine != -1

            count = 0
            while True:
                rec = rw.next_record()
                if not f.read_record(rec):
                    break
                assert rec.get_double(rIdTS)   == pytest.approx(100000.0 + count)
                assert rec.get_int32(rIdCnt)   == count
                assert rec.get_double(rIdSine) == pytest.approx(count * 0.5)
                count += 1

            assert count == TOTAL
