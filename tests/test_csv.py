# test_csv.py
# SPDX-License-Identifier: MIT
#
# Python port of CycFlow/Tests/test_Csv.cpp

import os
import time
import pytest
import cycflow


def read_lines(path: str) -> list[str]:
    with open(path, "r") as f:
        return [ln for ln in (line.rstrip("\r\n") for line in f) if ln]


class TestCsvWriter:
    @pytest.fixture(autouse=True)
    def tmp_file(self, tmp_path):
        self.path = str(tmp_path / "test_output.csv")
        yield
        if os.path.exists(self.path):
            os.remove(self.path)

    # -----------------------------------------------------------------------

    def test_creates_file_with_header(self):
        attrs = [
            cycflow.PAttr("ColInt", cycflow.DataType.Int32),
            cycflow.PAttr("ColDbl", cycflow.DataType.Double),
        ]
        rule = cycflow.RecRule(attrs)
        buf = cycflow.RecBuffer(rule, capacity=100)

        writer = cycflow.CsvWriter(self.path, buf, auto_start=True, batch_size=100)
        writer.finish()

        lines = read_lines(self.path)
        assert len(lines) == 1
        assert lines[0] == "TimeStamp,ColInt,ColDbl"

    def test_writes_formatted_data(self):
        attrs = [
            cycflow.PAttr("ID",    cycflow.DataType.Int32),
            cycflow.PAttr("Value", cycflow.DataType.Double),
        ]
        rule = cycflow.RecRule(attrs)
        rule_attrs = rule.get_attributes()
        id_ts    = rule_attrs[0].id
        id_id    = rule_attrs[1].id
        id_value = rule_attrs[2].id

        buf = cycflow.RecBuffer(rule, capacity=100)
        csv = cycflow.CsvWriter(self.path, buf, auto_start=True, batch_size=100)

        writer = cycflow.RecordWriter(buf, batch_capacity=10)

        rec = writer.next_record()
        rec.set_double(id_ts, 1.0)
        rec.set_int32(id_id, 10)
        rec.set_double(id_value, 3.14)
        writer.commit_record()

        rec2 = writer.next_record()
        rec2.set_double(id_ts, 1.0)
        rec2.set_int32(id_id, 20)
        rec2.set_double(id_value, 0.005)
        writer.commit_record()

        writer.flush()
        csv.finish()

        lines = read_lines(self.path)
        assert len(lines) == 3
        assert lines[0] == "TimeStamp,ID,Value"
        assert lines[1] == "1.000000,10,3.140000"
        assert lines[2] == "1.000000,20,0.005000"

    def test_appends_to_existing_file(self):
        # Pre-create a file with a matching header and one data row.
        with open(self.path, "w") as f:
            f.write("TimeStamp,ID,Value\n")
            f.write("1.000000,1,1.100000\n")

        attrs = [
            cycflow.PAttr("ID",    cycflow.DataType.Int32),
            cycflow.PAttr("Value", cycflow.DataType.Double),
        ]
        rule = cycflow.RecRule(attrs)
        rule_attrs = rule.get_attributes()
        id_ts    = rule_attrs[0].id
        id_id    = rule_attrs[1].id
        id_value = rule_attrs[2].id

        buf = cycflow.RecBuffer(rule, capacity=100)
        csv = cycflow.CsvWriter(self.path, buf, auto_start=True, batch_size=100)

        writer = cycflow.RecordWriter(buf, batch_capacity=10)
        rec = writer.next_record()
        rec.set_double(id_ts, 1.0)
        rec.set_int32(id_id, 2)
        rec.set_double(id_value, 2.2)
        writer.commit_record()
        writer.flush()
        csv.finish()

        lines = read_lines(self.path)
        assert len(lines) == 3
        assert lines[2] == "1.000000,2,2.200000"

    def test_handles_many_records(self):
        attrs = [cycflow.PAttr("X", cycflow.DataType.Int32)]
        rule = cycflow.RecRule(attrs)
        rule_attrs = rule.get_attributes()
        id_ts = rule_attrs[0].id
        id_x  = rule_attrs[1].id

        COUNT = 1000
        buf = cycflow.RecBuffer(rule, capacity=5000)
        csv = cycflow.CsvWriter(self.path, buf, auto_start=True, batch_size=200)

        writer = cycflow.RecordWriter(buf, batch_capacity=200)
        for i in range(COUNT):
            rec = writer.next_record()
            rec.set_double(id_ts, 1.0)
            rec.set_int32(id_x, i)
            writer.commit_record()

        writer.flush()
        csv.finish()

        lines = read_lines(self.path)
        assert len(lines) == COUNT + 1
        assert lines[-1] == "1.000000,999"

    @pytest.mark.parametrize("run", range(5))
    def test_long_running_producer_consumer(self, run):
        attrs = [cycflow.PAttr("Counter", cycflow.DataType.Int32)]
        rule = cycflow.RecRule(attrs)
        rule_attrs = rule.get_attributes()
        id_ts      = rule_attrs[0].id
        id_counter = rule_attrs[1].id

        TOTAL = 5000
        buf = cycflow.RecBuffer(rule, capacity=2000)
        writer = cycflow.RecordWriter(buf, batch_capacity=100, block_on_full=True)
        csv    = cycflow.CsvWriter(self.path, buf, auto_start=True, batch_size=100)

        for i in range(TOTAL):
            rec = writer.next_record()
            rec.set_double(id_ts, cycflow.get_current_epoch_time())
            rec.set_int32(id_counter, i)
            writer.commit_record()

        writer.flush()
        csv.finish()

        lines = read_lines(self.path)
        assert len(lines) == TOTAL + 1, \
            f"Expected {TOTAL + 1} lines, got {len(lines)}"

        last_parts = lines[-1].split(",")
        assert len(last_parts) >= 2
        assert int(last_parts[1]) == TOTAL - 1
