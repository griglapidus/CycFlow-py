"""
test_server.py — Python port of CycFlow/CycTestServer (v0.4.0).

Publishes a synthetic multi-channel data stream on TCP port 5000 at
~4 000 records/sec (200 records every 50 ms).

Schema (mirrors the C++ original):
    Counter   Int8   ×2  — two wrapping counters
    BitReg    UInt8      — 8 named bit flags (f0..f7)
    Voltage   Float      — sine wave around 12 V
    Current   Float      — sine wave around 3.2 A
    ADC ch0   Int16      — ADC-like integer reading
    Pressure  Double     — barometric pressure, Pa
    Const     Double     — constant 23.4 (probe for static-value rendering)

Uses the v0.4.0 zero-copy writer (RecordWriterZC) and the TcpServerManager
singleton, matching the updated C++ CycTestServer.

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
        ("BitReg",   cycflow.DataType.UInt8,
         ["f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7"]),
        ("Voltage",  cycflow.DataType.Float),
        ("Current",  cycflow.DataType.Float),
        ("ADC ch0",  cycflow.DataType.Int16),
        ("Pressure", cycflow.DataType.Double),
        ("Const",    cycflow.DataType.Double),
    ])

    id_counter  = cycflow.PReg.get_id("Counter")
    id_voltage  = cycflow.PReg.get_id("Voltage")
    id_current  = cycflow.PReg.get_id("Current")
    id_adc      = cycflow.PReg.get_id("ADC ch0")
    id_pressure = cycflow.PReg.get_id("Pressure")
    id_const    = cycflow.PReg.get_id("Const")
    bit_ids = [cycflow.PReg.get_id(f"f{i}") for i in range(8)]

    # Default-construct + init() — the v0.4.0 lazy-init pattern.
    buffer = cycflow.RecBuffer()
    buffer.init(rule, 10_000)

    # RecordWriterZC: zero-copy, synchronous, single-producer.
    writer = cycflow.RecordWriterZC()
    writer.init(buffer, batch_capacity=2000)

    # TcpServerManager singleton owns the io_context and TcpServer.
    mgr = cycflow.TcpServerManager.instance()
    mgr.start(PORT)
    mgr.register_buffer("Buffer_1", buffer, batch_size=500)

    print(f"Data Generator Server running on port {PORT}")
    print("Buffer registered as 'Buffer_1'")
    print("Press Ctrl+C to stop.\n")

    tick = 0
    try:
        while True:
            t0 = time.monotonic()

            for i in range(BATCH_SIZE):
                t = tick + i
                counter_val = t % 256
                if counter_val >= 128:
                    counter_val -= 256  # int8 wrap

                rec = writer.next_record()
                rec.set_int8(id_counter, counter_val,                    0)
                # second counter: counterVal + 128 with int8 wrap-around
                second = (counter_val + 128) % 256
                if second >= 128:
                    second -= 256
                rec.set_int8(id_counter, second,                         1)
                rec.set_float(id_voltage,  12.0 + 2.5 * math.sin(t * 0.05))
                rec.set_float(id_current,   3.2 + 0.8 * math.sin(t * 0.08))
                rec.set_int16(id_adc, int(math.sin(t * 0.02) * 5000 + 500))
                rec.set_double(id_pressure, 101.3 + 1.2 * math.sin(t * 0.03))
                rec.set_double(id_const, 23.4)
                for bit_idx, fid in enumerate(bit_ids):
                    rec.set_bit(fid, bool(t & (1 << bit_idx)))
                writer.commit_record()

            writer.flush()  # no-op on ZC writer but kept for symmetry
            tick += BATCH_SIZE

            remaining = INTERVAL_S - (time.monotonic() - t0)
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print(f"\nStopped after {tick} records.")
    finally:
        # ZC writer must be stopped before tearing down the buffer so any
        # thread blocked on backpressure unblocks cleanly.
        writer.stop()
        mgr.unregister_buffer("Buffer_1")
        mgr.stop()


if __name__ == "__main__":
    main()
