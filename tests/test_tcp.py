# test_tcp.py
# SPDX-License-Identifier: MIT
#
# Python port of CycFlow/Tests/test_Tcp.cpp

import time
import struct
import pytest
import cycflow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rule_sensor() -> cycflow.RecRule:
    attrs = [cycflow.PAttr("SensorValue", cycflow.DataType.Double, 1)]
    rule = cycflow.RecRule()
    rule.init(attrs)
    return rule


def make_rule_value() -> cycflow.RecRule:
    attrs = [cycflow.PAttr("Value", cycflow.DataType.Double, 1)]
    rule = cycflow.RecRule()
    rule.init(attrs)
    return rule


# ---------------------------------------------------------------------------
# TcpNetworkingTest  (basic discovery)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def networking_server():
    rule = make_rule_sensor()
    buf_a = cycflow.RecBuffer(rule, capacity=100)
    buf_b = cycflow.RecBuffer(rule, capacity=100)

    server = cycflow.TcpServer(port=15555)
    server.register_buffer("BufferA", buf_a, batch_size=10)
    server.register_buffer("BufferB", buf_b, batch_size=10)
    server.start()
    time.sleep(0.1)

    yield server, buf_a, buf_b

    server.stop()


@pytest.mark.usefixtures("networking_server")
class TestTcpNetworking:
    def test_request_buffer_list_returns_all_registered(self, networking_server):
        buf_list = cycflow.TcpServiceClient.request_buffer_list("127.0.0.1", 15555)
        assert len(buf_list) == 2
        assert "BufferA" in buf_list
        assert "BufferB" in buf_list

    def test_request_rec_rule_returns_correct_text(self, networking_server):
        rule = make_rule_sensor()
        rule_text = cycflow.TcpServiceClient.request_rec_rule("127.0.0.1", 15555, "BufferA")
        assert rule_text
        assert rule_text == rule.to_text()

    def test_request_rec_rule_returns_empty_for_unknown_buffer(self, networking_server):
        rule_text = cycflow.TcpServiceClient.request_rec_rule("127.0.0.1", 15555, "UnknownBuffer")
        assert rule_text == ""


# ---------------------------------------------------------------------------
# TcpIntegrationTest
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def integration_server():
    rule = make_rule_value()
    source_buf = cycflow.RecBuffer(rule, capacity=1000)

    server = cycflow.TcpServer(port=15556)
    server.register_buffer("TestStream", source_buf, batch_size=20)
    server.start()
    time.sleep(0.1)

    yield server, source_buf, rule

    server.stop()


def push_test_data(buf: cycflow.RecBuffer, value: float, timestamp: float = 1.0):
    raw = struct.pack("<dd", timestamp, value)
    # Use RecordWriter to push one record
    writer = cycflow.RecordWriter(buf, batch_capacity=1)
    rule_attrs = buf.get_rule().get_attributes()
    id_ts  = rule_attrs[0].id
    id_val = rule_attrs[1].id
    rec = writer.next_record()
    rec.set_double(id_ts, timestamp)
    rec.set_double(id_val, value)
    writer.commit_record()
    writer.flush()


@pytest.mark.usefixtures("integration_server")
class TestTcpIntegration:
    def test_client_can_discover_buffers(self, integration_server):
        lst = cycflow.TcpServiceClient.request_buffer_list("127.0.0.1", 15556)
        assert len(lst) == 1
        assert lst[0] == "TestStream"

    def test_client_can_retrieve_rec_rule(self, integration_server):
        _, _, rule = integration_server
        received_text = cycflow.TcpServiceClient.request_rec_rule(
            "127.0.0.1", 15556, "TestStream"
        )
        assert received_text

        local_rule = cycflow.RecRule.from_text(received_text)
        assert local_rule.get_rec_size() == rule.get_rec_size()
        attrs = local_rule.get_attributes()
        assert len(attrs) == 2
        assert attrs[1].name == "Value"

    def test_receiver_gets_streamed_data(self, integration_server):
        _, source_buf, _ = integration_server

        receiver = cycflow.TcpDataReceiver(buffer_capacity=1000, writer_batch_size=20)
        assert receiver.connect("127.0.0.1", 15556, "TestStream")

        dest_buf = receiver.get_buffer()
        assert dest_buf is not None

        RECORD_COUNT = 50
        for i in range(RECORD_COUNT):
            push_test_data(source_buf, float(i * 1.1), float(i))

        retries = 0
        while dest_buf.get_total_written() < RECORD_COUNT and retries < 10:
            time.sleep(0.05)
            retries += 1

        assert dest_buf.get_total_written() == RECORD_COUNT, "Buffer is missing data"

        snap = dest_buf.snapshot()
        id_val = -1
        for a in dest_buf.get_rule():
            if a.name == "Value":
                id_val = a.id

        assert id_val != -1
        assert snap["Value"][0]  == pytest.approx(0.0)
        assert snap["Value"][10] == pytest.approx(11.0)

        receiver.stop()

    def test_connect_to_nonexistent_buffer_returns_error(self, integration_server):
        receiver = cycflow.TcpDataReceiver(buffer_capacity=1000)
        connected = receiver.connect("127.0.0.1", 15556, "MissingBuffer")

        assert not connected
        assert receiver.get_buffer() is None
