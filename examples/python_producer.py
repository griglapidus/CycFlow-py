"""
Subclass BatchRecordProducer in Python and publish a high-throughput stream.

Shows the pybind11 trampoline in action: Python overrides define_rule() and
produce_batch(), and CycLib drives them from its background thread.
"""
import time

import numpy as np
import cycflow


class SineProducer(cycflow.BatchRecordProducer):
    """Generates a sine wave as fast as the buffer can consume it."""

    def __init__(self, buffer_capacity: int = 10_000, batch_size: int = 1000):
        super().__init__(buffer_capacity, batch_size)
        self._t = 0

    def define_rule(self) -> cycflow.RecRule:
        return cycflow.make_rule([
            ("Sample", cycflow.DataType.Float),
            ("Index",  cycflow.DataType.UInt32),
        ])

    def produce_batch(self, batch: cycflow.WriteBatch) -> int:
        arr = batch.as_numpy()
        n = batch.capacity
        idx = np.arange(self._t, self._t + n, dtype=np.uint32)
        arr["Sample"] = np.sin(idx * 0.01).astype(np.float32)
        arr["Index"]  = idx
        self._t += n
        return n


def main() -> None:
    producer = SineProducer(buffer_capacity=20_000, batch_size=1000)

    with cycflow.TcpServer(port=5000) as server:
        server.register_buffer("SineStream", producer.get_buffer(),
                               batch_size=1000)
        producer.start()

        print("Publishing on tcp://127.0.0.1:5000/SineStream ...")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nStopping.")
            producer.stop()
            producer.join()


if __name__ == "__main__":
    main()
