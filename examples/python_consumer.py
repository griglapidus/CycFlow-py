"""
A Python-defined RecordConsumer that tracks running statistics.

This example uses the single-record form (RecordConsumer) rather than batch —
it's simpler, and the per-call Python overhead doesn't matter unless you're
processing hundreds of thousands of records/sec. For that, subclass
BatchRecordConsumer and iterate over the batch manually using RecRule offsets.
"""
import threading
import time

import cycflow


class RunningStats(cycflow.RecordConsumer):
    """Track the running mean/min/max of a named float/double field."""

    def __init__(self, buffer, field_name: str, batch_size: int = 100):
        super().__init__(buffer, reader_batch_size=batch_size)
        self._field_id = cycflow.PReg.get_id(field_name)
        self._lock = threading.Lock()
        self._count = 0
        self._sum = 0.0
        self._min = float("inf")
        self._max = float("-inf")

    # Called on the consumer thread (GIL is held by pybind11 for trampolines).
    def consume_record(self, rec: cycflow.Record) -> None:
        v = rec.get_value(self._field_id)  # generic double getter
        with self._lock:
            self._count += 1
            self._sum += v
            if v < self._min: self._min = v
            if v > self._max: self._max = v

    def snapshot(self) -> dict:
        with self._lock:
            mean = self._sum / self._count if self._count else 0.0
            return {
                "count": self._count,
                "mean":  mean,
                "min":   self._min if self._count else 0.0,
                "max":   self._max if self._count else 0.0,
            }


def main() -> None:
    with cycflow.TcpDataReceiver(buffer_capacity=20_000) as rx:
        if not rx.connect("127.0.0.1", 5000, "SensorStream"):
            raise SystemExit("Failed to connect")

        stats = RunningStats(rx.get_buffer(), field_name="Voltage")
        stats.start()
        try:
            for _ in range(10):
                time.sleep(1.0)
                s = stats.snapshot()
                print(f"n={s['count']:7d}  mean={s['mean']:7.3f}  "
                      f"min={s['min']:7.3f}  max={s['max']:7.3f}")
        finally:
            stats.finish()


if __name__ == "__main__":
    main()
