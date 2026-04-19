# cycflow — Python bindings for CycFlow

Python bindings for [CycFlow / CycLib](https://github.com/griglapidus/CycFlow)
covering both the client and server sides:

- connect to a CycFlow server and consume live data (`TcpDataReceiver`)
- publish data as a CycFlow server (`TcpServer` + `RecordWriter`)
- read/write `.cbf` and `.csv` files (`CbfReader`, `CbfWriter`, `CsvWriter`, `CbfFile`)
- subclass `RecordProducer` / `RecordConsumer` in pure Python

## Installation

```bash
pip install .
# iterative development:
pip install -e . --no-build-isolation
```

## API overview

### Core

| Python | C++ | Purpose |
|---|---|---|
| `DataType` | `cyc::DataType` | Enum: `Undefine, Bool, Char, Void, Int8..UInt64, Float, Double, Ptr` |
| `PAttr(name, type, count=1)` | `cyc::PAttr` | Plain field (name ≤ 25 chars) |
| `PAttr(name, type, bit_defs)` | `cyc::PAttr` | Integer field with named bits |
| `PReg` | `cyc::PReg` | Global name → id registry |
| `BitRef` | `cyc::BitRef` | `{field_id, bit_pos}` |
| `RecRule` | `cyc::RecRule` | Schema, `.to_text()/.from_text()` |
| `Record` | `cyc::Record` | View over a record (typed + generic + bit API) |

### Buffering & I/O

| Python | C++ | Purpose |
|---|---|---|
| `BufferClient(cb)` | `cyc::IRecBufferClient` | Notification subscriber |
| `RecBuffer` | `cyc::RecBuffer` | Ring buffer of records |
| `RecordBatch` | `RecordReader::RecordBatch` | Read-only contiguous block |
| `RecordReader` | `cyc::RecordReader` | `next_record/next_batch/next_batch_copy` |
| `WriteBatch` | `RecordWriter::RecordBatch` | Writable contiguous block |
| `RecordWriter` | `cyc::RecordWriter` | Double-buffered writer |
| `RecordProducer` | `cyc::RecordProducer` | **Python-subclassable** abstract producer |
| `BatchRecordProducer` | `cyc::BatchRecordProducer` | **Python-subclassable** bulk producer |
| `RecordConsumer` | `cyc::RecordConsumer` | **Python-subclassable** abstract consumer |
| `BatchRecordConsumer` | `cyc::BatchRecordConsumer` | **Python-subclassable** bulk consumer |

### TCP

| Python | C++ | Purpose |
|---|---|---|
| `TcpServiceClient.request_buffer_list(host, port)` | `cyc::TcpServiceClient` | Discovery |
| `TcpServiceClient.request_rec_rule(host, port, name)` | `cyc::TcpServiceClient` | Schema lookup |
| `TcpDataReceiver(buffer_capacity, writer_batch_size)` | `cyc::TcpDataReceiver` | Client |
| `TcpServer(port)` | `cyc::TcpServer` (+ owned io_context/thread) | Server |

### CBF / CSV

| Python | C++ | Purpose |
|---|---|---|
| `CbfReader(path, ...)` | `cyc::CbfReader` | Streaming offline reader |
| `CbfWriter(path, buffer, ...)` | `cyc::CbfWriter` | Async CBF writer |
| `CsvWriter(path, buffer, ...)` | `cyc::CsvWriter` | Async CSV writer |
| `CbfFile` | `cyc::CbfFile` | Low-level section-by-section API |
| `CbfMode`, `CbfSectionType`, `CbfSectionHeader`, `CBF_SECTION_MARKER` | — | Low-level types |
| `read_cbf_to_array(path)` | — (helper) | Whole file → one numpy array |

### Python helpers

| Function | Purpose |
|---|---|
| `make_rule(attrs)` | Tuple-based `RecRule` factory (arrays, bit_defs supported) |
| `discover(host, port)` | `{buffer_name: RecRule}` |
| `to_dataframe(src)` | numpy / `RecBuffer` → `pandas.DataFrame` |
| `publish_dataframe(df, writer)` | Bulk DataFrame → `RecordWriter` via zero-copy batch fill |
| `stream(host, port, name, ...)` | `asyncio` generator of numpy batches |

## Quick start

### 1. Connect and consume

```python
import cyclib
with cyclib.TcpDataReceiver(buffer_capacity=20_000) as rx:
    rx.connect("127.0.0.1", 5000, "SensorStream")
    reader = cyclib.RecordReader(rx.get_buffer(), batch_capacity=1000)
    for _ in range(5):
        batch = reader.next_batch_copy(1000)
        if batch is None: break
        print(batch["Voltage"].mean())
```

### 2. Publish as a server

```python
import cyclib, math, time

rule = cyclib.make_rule([
    ("Voltage",  cyclib.DataType.Float),
    ("Pressure", cyclib.DataType.Double),
])
buffer = cyclib.RecBuffer(rule, capacity=10_000)
writer = cyclib.RecordWriter(buffer, batch_capacity=1000)
idV = cyclib.PReg.get_id("Voltage")
idP = cyclib.PReg.get_id("Pressure")

with cyclib.TcpServer(port=5000) as server:
    server.register_buffer("SensorStream", buffer, batch_size=500)
    for i in range(100_000):
        rec = writer.next_record()
        rec.set_float(idV, 12.0 + math.sin(i*0.01))
        rec.set_double(idP, 101.3)
        writer.commit_record()
        if i % 1000 == 0:
            writer.flush()
```

### 3. Record a stream to files

```python
cbf = cyclib.CbfWriter("session.cbf", buf); cbf.set_alias("TestRun")
csv = cyclib.CsvWriter("session.csv", buf)
# ... both run on background threads
cbf.finish(); csv.finish()
```

### 4. Subclass in Python

```python
class Sine(cyclib.BatchRecordProducer):
    def define_rule(self):
        return cyclib.make_rule([("X", cyclib.DataType.Float)])
    def produce_batch(self, batch):
        arr = batch.as_numpy()
        arr["X"] = np.linspace(0, 1, len(arr), dtype="f4")
        return len(arr)

p = Sine(buffer_capacity=10_000, writer_batch_size=1000)
p.start()
```

### 5. Async streaming

```python
import asyncio, cyclib

async def main():
    async for batch in cyclib.stream("127.0.0.1", 5000, "SensorStream"):
        print(batch["Voltage"].mean())

asyncio.run(main())
```

### 6. Bit fields

```python
rule = cyclib.make_rule([
    ("StatusReg", cyclib.DataType.UInt8, ["tx", "rx", "4", "errFlag"]),
])
rec = reader.next_record()
print(rec.get_bit(cyclib.PReg.get_id("tx")))
```

### 7. DataFrame → TCP in one line

```python
cyclib.publish_dataframe(my_df, writer)
```

### 8. Low-level CBF inspection

```python
with cyclib.CbfFile() as f:
    f.open("session.cbf", cyclib.CbfMode.Read)
    while (h := f.read_section_header()) is not None:
        print(h)
        if h.type == int(cyclib.CbfSectionType.Header):
            rule = f.read_rule(h)
            print([a.name for a in rule])
        else:
            f.skip_section(h)
```

## Type mapping

| CycLib | C++ | NumPy | In dtype |
|---|---|---|---|
| `dtUndefine` | — | — | ✗ |
| `dtBool` | `bool` | `?` | ✓ |
| `dtChar` | `char` | `S1` | ✓ |
| `dtVoid` | — | — | ✗ |
| `dtInt8..Int64` | `int8_t..int64_t` | `<i1..<i8` | ✓ |
| `dtUInt8..UInt64` | `uint8_t..uint64_t` | `<u1..<u8` | ✓ |
| `dtFloat` | `float` | `<f4` | ✓ |
| `dtDouble` | `double` | `<f8` | ✓ |
| `dtPtr` | `void*` | — | ✗ (intentional) |

Array fields (`count > 1`) become subarray dtype: `("<f4", (N,))`.
Fields with bit_defs are represented as their containing integer; individual
bits are accessed via `Record.get_bit(PReg.get_id(name))`.

## Architecture

```
        ┌──────────────────────────┐
        │  Python producer class   │   (subclass of BatchRecordProducer)
        └────────────┬─────────────┘
                     │ define_rule / produce_batch
        ┌────────────▼──────────────┐
        │       RecordWriter        │───push──► RecBuffer ──┬─► RecordReader ─► numpy/pandas
        └───────────────────────────┘                       ├─► CbfWriter
                     ▲                                      ├─► CsvWriter
                     │                                      └─► TcpServer ── TCP ──► receiver
        ┌────────────┴──────────────┐
        │       TcpDataReceiver     │◄── TCP ──  (remote server)
        └───────────────────────────┘
```

- **Zero-copy read**: `RecordReader.next_batch()` returns a numpy view.
  Valid until the next call on the same reader.
- **Zero-copy write**: `RecordWriter.next_batch().as_numpy()` returns a writable
  numpy view. Fill, then `commit_batch(count)`.
- **Safe copies**: `RecBuffer.snapshot()`, `RecordReader.next_batch_copy()`,
  `read_cbf_to_array()`.

## Examples

- `examples/tcp_sync.py` — discovery + synchronous client
- `examples/tcp_async.py` — asyncio streaming client
- `examples/tcp_publish.py` — TCP server that publishes sensor data
- `examples/python_producer.py` — `BatchRecordProducer` subclass
- `examples/python_consumer.py` — `RecordConsumer` subclass (running stats)
- `examples/record_to_file.py` — dump TCP stream to CBF + CSV simultaneously
- `examples/read_cbf.py` — offline analysis of a `.cbf` file
- `examples/cbf_inspect.py` — low-level section-by-section CBF walker
- `examples/bit_fields.py` — named bits inside integer fields

## Limitations

- `dtPtr` is never exposed across the Python boundary.
- `dtChar` fields surface as `S1` in numpy; decode with `bytes().decode()` as needed.
- `Record` holds a reference to the owning `RecRule` — don't outlive the parent reader.
- `TcpServer` uses its own owned ASIO `io_context`; if you need to share one with other
  C++ components, modify `PyTcpServer` in `bind_server.cpp`.

## License

MIT.
