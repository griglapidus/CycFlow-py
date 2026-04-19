"""
test_server.py — Python port of CycFlow/CycTestServer.

Publishes a synthetic multi-channel data stream on TCP port 5000 at
~4 000 records/sec (200 records every 50 ms).

Schema (mirrors the C++ original):
    Counter   Int8   ×2  — two wrapping counters
    Voltage   Float      — sine wave around 12 V
    Current   Float      — sine wave around 3.2 A
    ADC ch0   Int16      — ADC-like integer reading
    Pressure  Double     — barometric pressure, Pa

Run this first, then connect with any client example:
    python tcp_sync.py
    python live_plot.py
    python record_to_file.py
"""
import math
import time

import cycflow

PORT       = 5000
BATCH_SIZE = 200
INTERVAL_S = 0.050   # 50 ms  →  ~4 000 records/sec


def main() -> None:
    rule = cycflow.make_rule([
        ("Counter",  cycflow.DataType.Int8,   2),
        ("BitReg", cycflow.DataType.UInt8, ["f0","f1","f2","f3","f4","f5","f6","f7"]),
        ("Voltage",  cycflow.DataType.Float),
        ("Current",  cycflow.DataType.Float),
        ("ADC ch0",  cycflow.DataType.Int16),
        ("Pressure", cycflow.DataType.Double),
    ])

    id_counter  = cycflow.PReg.get_id("Counter")
    id_voltage  = cycflow.PReg.get_id("Voltage")
    id_current  = cycflow.PReg.get_id("Current")
    id_adc      = cycflow.PReg.get_id("ADC ch0")
    id_pressure = cycflow.PReg.get_id("Pressure")
    id_f0 = cycflow.PReg.get_id("f0")
    id_f1 = cycflow.PReg.get_id("f1")
    id_f2 = cycflow.PReg.get_id("f2")
    id_f3 = cycflow.PReg.get_id("f3")
    id_f4 = cycflow.PReg.get_id("f4")
    id_f5 = cycflow.PReg.get_id("f5")
    id_f6 = cycflow.PReg.get_id("f6")
    id_f7 = cycflow.PReg.get_id("f7")

    buffer = cycflow.RecBuffer(rule, capacity=10_000)
    writer = cycflow.RecordWriter(buffer, batch_capacity=2000)

    with cycflow.TcpServer(port=PORT) as server:
        server.register_buffer("Buffer_1", buffer, batch_size=1000)
        print(f"Test server running on port {PORT}")
        print("Buffer registered as 'Buffer_1'")
        print("Press Ctrl+C to stop.\n")

        tick = 0
        try:
            while True:
                t0 = time.monotonic()

                for i in range(BATCH_SIZE):
                    t = tick + i
                    rec = writer.next_record()
                    rec.set_int8(id_counter,  t % 128,         0)
                    rec.set_int8(id_counter, (t + 20) % 128,   1)
                    rec.set_float(id_voltage,  12.0 + 2.5 * math.sin(t * 0.05))
                    rec.set_float(id_current,   3.2 + 0.8 * math.sin(t * 0.08))
                    rec.set_int16(id_adc, int(math.sin(t * 0.02) * 5000 + 500))
                    rec.set_double(id_pressure, 101.3 + 1.2 * math.sin(t * 0.03))
                    for bit_idx, fid in enumerate([id_f0, id_f1, id_f2, id_f3, id_f4, id_f5, id_f6, id_f7]):
                        rec.set_bit(fid, bool(t & (1 << bit_idx)))
                    writer.commit_record()

                writer.flush()
                tick += BATCH_SIZE

                # Sleep for the remainder of the interval to keep stable FPS.
                remaining = INTERVAL_S - (time.monotonic() - t0)
                if remaining > 0:
                    time.sleep(remaining)

        except KeyboardInterrupt:
            writer.flush()
            print(f"\nStopped after {tick} records.")


if __name__ == "__main__":
    main()
