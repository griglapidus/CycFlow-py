"""
Discovery + synchronous TCP streaming.
"""
import time
import cyclib


def main() -> None:
    host, port = "127.0.0.1", 5000

    # 1. Discovery.
    buffers = cyclib.TcpServiceClient.request_buffer_list(host, port)
    print("Available buffers:", buffers)
    if not buffers:
        raise SystemExit("Server has no published buffers")

    name = buffers[0]
    schema_text = cyclib.TcpServiceClient.request_rec_rule(host, port, name)
    print(f"\nSchema for '{name}':\n{schema_text}")

    # 2. Connect and read.
    with cyclib.TcpDataReceiver(buffer_capacity=20_000) as rx:
        if not rx.connect(host, port, name):
            raise SystemExit(f"Failed to connect to {name}")

        time.sleep(1.0)

        buf = rx.get_buffer()
        print(f"\nBuffered: {buf.size()}/{buf.capacity()} records")

        reader = cyclib.RecordReader(buf, batch_capacity=1000)
        for _ in range(5):
            batch = reader.next_batch_copy(1000, wait=True)
            if batch is None:
                break
            print(f"  got {len(batch)} records, "
                  f"mean(Voltage)={batch['Voltage'].mean():.3f}")


if __name__ == "__main__":
    main()
