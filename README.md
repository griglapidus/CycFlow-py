# cycflow

> Python bindings for [CycLib](https://github.com/griglapidus/CycFlow) — a high-performance C++ library for streaming record-based data.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![NumPy](https://img.shields.io/badge/numpy-1.22%2B-orange)

`cycflow` lets you produce, consume, and analyze continuous typed data streams with **zero-copy NumPy integration**, **live TCP networking**, and **CBF file I/O** — all from Python.

---

## Features

- **Zero-copy reads** — `RecordReader.next_batch()` returns a NumPy structured array view directly into the ring buffer
- **Zero-copy writes** — fill a writable NumPy batch and commit it in one call
- **TCP streaming** — connect to remote CycFlow servers or publish your own streams to multiple clients
- **Python subclassing** — implement producers and consumers in pure Python via pybind11 trampolines
- **CBF / CSV file I/O** — read and write the CycFlow Binary Format with background-thread writers
- **asyncio support** — `async for` generator over live TCP streams
- **Bit fields** — named bits inside integer fields with typed accessors
- **Pandas integration** — convert buffers and CBF files to DataFrames in one line

---

## Installation

```bash
pip install .
```

Development mode:

```bash
pip install -e . --no-build-isolation
```

With optional extras:

```bash
pip install .[plot]   # pandas + matplotlib
pip install .[dev]    # pytest + pytest-asyncio
```

**Build requirements:** CMake ≥ 3.15, a C++17 compiler, pybind11 ≥ 3.0.0, scikit-build-core ≥ 0.10.

---

## Quick Start

### Connect and consume a stream

```python
import cycflow

with cycflow.TcpDataReceiver(buffer_capacity=20_000) as rx:
    rx.connect("127.0.0.1", 5000, "SensorStream")
    reader = cycflow.RecordReader(rx.get_buffer(), batch_capacity=1000)

    batch = reader.next_batch_copy(1000)   # safe copy
    if batch is not None:
        print(batch["Voltage"].mean())
```

### Publish a stream

```python
import math, cycflow

rule = cycflow.make_rule([
    ("Voltage",  cycflow.DataType.Float),
    ("Pressure", cycflow.DataType.Double),
])
buffer = cycflow.RecBuffer(rule, capacity=10_000)
writer = cycflow.RecordWriter(buffer, batch_capacity=1000)
idV    = cycflow.PReg.get_id("Voltage")
idP    = cycflow.PReg.get_id("Pressure")

with cycflow.TcpServer(port=5000) as server:
    server.register_buffer("SensorStream", buffer, batch_size=500)
    for i in range(100_000):
        rec = writer.next_record()
        rec.set_float(idV,  12.0 + math.sin(i * 0.01))
        rec.set_double(idP, 101.3)
        writer.commit_record()
        if i % 1000 == 0:
            writer.flush()
```

### Implement a producer in Python

```python
import numpy as np, cycflow

class SineProducer(cycflow.BatchRecordProducer):
    def define_rule(self):
        return cycflow.make_rule([("X", cycflow.DataType.Float)])

    def produce_batch(self, batch):
        arr = batch.as_numpy()
        arr["X"] = np.linspace(0, 1, len(arr), dtype="f4")
        return len(arr)

producer = SineProducer(buffer_capacity=10_000, writer_batch_size=1000)
producer.start()
```

### Async streaming

```python
import asyncio, cycflow

async def main():
    async for batch in cycflow.stream("127.0.0.1", 5000, "SensorStream"):
        print(batch["Voltage"].mean())

asyncio.run(main())
```

### Record a stream to CBF and CSV

```python
cbf = cycflow.CbfWriter("session.cbf", buffer)
csv = cycflow.CsvWriter("session.csv", buffer)
# ... both write in background threads
cbf.finish()
csv.finish()
```

### Read a CBF file

```python
arr = cycflow.read_cbf_to_array("session.cbf")
df  = cycflow.to_dataframe(arr)
print(df.describe())
```

### Bit fields

```python
rule = cycflow.make_rule([
    ("StatusReg", cycflow.DataType.UInt8, ["tx", "rx", "4", "errFlag"]),
])
rec = reader.next_record()
print(rec.get_bit(cycflow.PReg.get_id("tx")))
```

---

## Architecture

```
┌──────────────────────────┐
│  Python producer class   │   (subclass BatchRecordProducer)
└────────────┬─────────────┘
             │ define_rule / produce_batch
┌────────────▼──────────────┐
│       RecordWriter        │───push──► RecBuffer ──┬─► RecordReader ─► numpy/pandas
└───────────────────────────┘                       ├─► CbfWriter
             ▲                                      ├─► CsvWriter
             │                                      └─► TcpServer ── TCP ──► client
┌────────────┴──────────────┐
│       TcpDataReceiver     │◄── TCP ──  (remote CycFlow server)
└───────────────────────────┘
```

**Zero-copy read:** `RecordReader.next_batch()` returns a NumPy view valid until the next call on the same reader.

**Zero-copy write:** `RecordWriter.next_batch().as_numpy()` returns a writable NumPy view; call `commit_batch(n)` when done.

**Safe copies:** `RecBuffer.snapshot()`, `RecordReader.next_batch_copy()`, `read_cbf_to_array()`.

---

## API Reference

### Core types

| Class | Description |
|---|---|
| `DataType` | Enum of field types: `Bool`, `Char`, `Int8`…`UInt64`, `Float`, `Double` |
| `PAttr(name, type, count=1)` | Plain field descriptor |
| `PAttr(name, type, bit_defs)` | Integer field with named bits |
| `PReg` | Global field-name ↔ ID registry (`PReg.get_id(name)`) |
| `RecRule` | Schema: ordered list of `PAttr`, computed offsets and record size |
| `Record` | Single-record accessor (`get_float`, `set_double`, `get_bit`, …) |

### Buffers and I/O

| Class | Description |
|---|---|
| `RecBuffer(rule, capacity)` | Ring buffer of typed records |
| `RecordReader(buffer, batch_capacity)` | Read batches; `next_batch()` → zero-copy view, `next_batch_copy()` → safe copy |
| `RecordWriter(buffer, batch_capacity)` | Write records with `next_record()` / `commit_record()` or batch API |
| `WriteBatch` | Writable NumPy batch returned by `writer.next_batch()` |
| `BufferClient(callback)` | Notification subscriber for buffer events |

### Producers and consumers

| Class | Description |
|---|---|
| `BatchRecordProducer` | Subclass and implement `define_rule()` + `produce_batch(batch)` |
| `RecordProducer` | Subclass and implement `define_rule()` + `produce_record(rec)` |
| `BatchRecordConsumer` | Background consumer — implement `consume_batch(batch)` |
| `RecordConsumer` | Background consumer — implement `consume_record(rec)` |

### TCP networking

| Symbol | Description |
|---|---|
| `TcpServer(port)` | Serve one or more buffers to multiple simultaneous clients |
| `TcpDataReceiver(buffer_capacity)` | Connect to a server and stream records into a local buffer |
| `TcpServiceClient` | Discover available buffers (`request_buffer_list`) and fetch schemas (`request_rec_rule`) |
| `cycflow.discover(host, port)` | One-shot helper → `{buffer_name: RecRule}` |
| `cycflow.stream(host, port, name)` | Async generator yielding NumPy batches |

### File I/O

| Symbol | Description |
|---|---|
| `CbfWriter(path, buffer)` | Background-thread CBF writer attached to a buffer |
| `CsvWriter(path, buffer)` | Background-thread CSV writer attached to a buffer |
| `CbfReader(path)` | Sequential streaming CBF reader |
| `CbfFile` | Low-level CBF section-by-section inspector |
| `read_cbf_to_array(path)` | Load an entire CBF file into a NumPy structured array |

### Python helpers

| Function | Description |
|---|---|
| `make_rule(fields)` | Build a `RecRule` from `[(name, dtype), …]` or `[(name, dtype, count), …]` |
| `discover(host, port)` | List remote buffers and their schemas |
| `to_dataframe(array)` | Convert a NumPy structured array or `RecBuffer` to a pandas DataFrame |
| `publish_dataframe(df, writer)` | Push a DataFrame into a `RecordWriter` via zero-copy batch fill |
| `stream(host, port, name)` | Async generator over a live TCP stream |

---

## NumPy type mapping

| `DataType` | NumPy dtype | In structured array |
|---|---|---|
| `Bool` | `?` | ✓ |
| `Char` | `S1` | ✓ |
| `Int8` / `UInt8` | `<i1` / `<u1` | ✓ |
| `Int16` / `UInt16` | `<i2` / `<u2` | ✓ |
| `Int32` / `UInt32` | `<i4` / `<u4` | ✓ |
| `Int64` / `UInt64` | `<i8` / `<u8` | ✓ |
| `Float` | `<f4` | ✓ |
| `Double` | `<f8` | ✓ |
| `Ptr` | — | ✗ (never exposed) |

Array fields (`count > 1`) become subarray dtypes, e.g. `("<f4", (3,))`.
Bit-field integers appear as their containing integer type; individual bits are accessed via `Record.get_bit(PReg.get_id(name))`.

---

## Examples

| File | Description |
|---|---|
| [`tcp_sync.py`](examples/tcp_sync.py) | Service discovery + synchronous client |
| [`tcp_async.py`](examples/tcp_async.py) | asyncio streaming client |
| [`tcp_publish.py`](examples/tcp_publish.py) | TCP server broadcasting sensor data |
| [`python_producer.py`](examples/python_producer.py) | `BatchRecordProducer` subclass (sine wave) |
| [`python_consumer.py`](examples/python_consumer.py) | `RecordConsumer` subclass with running stats |
| [`record_to_file.py`](examples/record_to_file.py) | Dump a live TCP stream to CBF + CSV simultaneously |
| [`read_cbf.py`](examples/read_cbf.py) | Offline CBF analysis |
| [`cbf_inspect.py`](examples/cbf_inspect.py) | Low-level CBF section walker |
| [`bit_fields.py`](examples/bit_fields.py) | Named bits inside integer fields |
| [`live_plot.py`](examples/live_plot.py) | Real-time matplotlib animation |
| [`test_server.py`](examples/test_server.py) | Python port of `CycTestServer` — synthetic multi-channel stream on port 5000 (~4k records/sec); use as a data source when testing other examples |

---

## Testing

```bash
pytest tests/
pytest tests/test_core.py -v    # schema, record, and buffer tests
pytest tests/test_cbf.py  -v    # CBF file write/read round-trips
pytest tests/test_csv.py  -v    # CSV formatting and appending
pytest tests/test_tcp.py  -v    # TCP discovery and streaming
```

---

## Known limitations

- `dtPtr` is never exposed across the Python boundary.
- `dtChar` surfaces as `S1` in NumPy; decode with `bytes.decode()` as needed.
- `Record` holds a reference to its owning `RecRule` — don't outlive the parent reader.
- `TcpServer` owns its ASIO `io_context`; to share one with other C++ components, modify `PyTcpServer` in `src/bind_server.cpp`.

---

## License

[MIT](LICENSE.txt) — © 2026 Grigorii Lapidus
