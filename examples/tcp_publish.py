"""
Publish a data stream as a CycFlow TCP server.

Mirrors the C++ example from the CycFlow README: define a schema, create
a RecBuffer, wire it into a RecordWriter, register with a TcpServer, and
generate records in a loop.
"""
import math
import time

import cyclib


def main() -> None:
    # 1. Schema.
    rule = cyclib.make_rule([
        ("Counter",  cyclib.DataType.Int8, 2),   # array of 2
        ("Voltage",  cyclib.DataType.Float),
        ("Pressure", cyclib.DataType.Double),
    ])
    id_counter  = cyclib.PReg.get_id("Counter")
    id_voltage  = cyclib.PReg.get_id("Voltage")
    id_pressure = cyclib.PReg.get_id("Pressure")

    # 2. Buffer + writer.
    buffer = cyclib.RecBuffer(rule, capacity=10_000)
    writer = cyclib.RecordWriter(buffer, batch_capacity=2000)

    # 3. Server.
    with cyclib.TcpServer(port=5000) as server:
        server.register_buffer("SensorStream", buffer, batch_size=1000)
        print("Publishing on tcp://127.0.0.1:5000/SensorStream ...")

        # 4. Produce records.
        try:
            t0 = time.time()
            i = 0
            while True:
                rec = writer.next_record()
                rec.set_int8(id_counter, i & 0x7F, 0)
                rec.set_int8(id_counter, (i >> 7) & 0x7F, 1)
                rec.set_float(id_voltage, 12.0 + math.sin(i * 0.01))
                rec.set_double(id_pressure, 101.3 + 0.5 * math.cos(i * 0.02))
                writer.commit_record()

                i += 1
                if i % 2000 == 0:
                    writer.flush()
                    print(f"  published {i} records in {time.time() - t0:.1f}s")
                time.sleep(0.001)
        except KeyboardInterrupt:
            writer.flush()
            print("\nStopping.")


if __name__ == "__main__":
    main()
