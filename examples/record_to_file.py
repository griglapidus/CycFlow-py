"""
Record an incoming TCP stream to CBF and CSV simultaneously.

Both writers are background consumers attached to the same RecBuffer — they
don't block each other and don't block the network receiver.
"""
import time
import cyclib


def main() -> None:
    with cyclib.TcpDataReceiver(buffer_capacity=20_000) as rx:
        if not rx.connect("127.0.0.1", 5000, "SensorStream"):
            raise SystemExit("Failed to connect")

        buf = rx.get_buffer()

        # Both writers start their own background thread.
        cbf = cyclib.CbfWriter("session.cbf", buf,
                               auto_start=False, batch_size=1000)
        cbf.set_alias("TestRun")
        cbf.start()

        csv = cyclib.CsvWriter("session.csv", buf,
                               auto_start=True, batch_size=500)

        print("Recording for 5 seconds...")
        time.sleep(5.0)

        # finish() drains everything up to the current cursor, then stops.
        cbf.finish()
        csv.finish()
        print("Done. Wrote session.cbf and session.csv")


if __name__ == "__main__":
    main()
