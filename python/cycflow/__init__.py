"""
cycflow — Python bindings for CycFlow / CycLib.

Primary usage patterns:

    # 1. Discovery (no background threads)
    buffers = cycflow.TcpServiceClient.request_buffer_list("127.0.0.1", 5000)
    rules   = cycflow.discover("127.0.0.1", 5000)

    # 2. Live TCP streaming (client)
    with cycflow.TcpDataReceiver(buffer_capacity=20_000) as rx:
        rx.connect("127.0.0.1", 5000, "SensorStream")
        reader = cycflow.RecordReader(rx.get_buffer(), batch_capacity=1000)
        while rx.is_running():
            batch = reader.next_batch_copy(1000, wait=True)
            if batch is None: break
            print(batch["Voltage"].mean())

    # 3. Publishing data over TCP (server)
    rule   = cycflow.make_rule([("Voltage", cycflow.DataType.Float)])
    buffer = cycflow.RecBuffer(rule, capacity=10_000)
    writer = cycflow.RecordWriter(buffer, batch_capacity=1000)
    with cycflow.TcpServer(port=5000) as srv:
        srv.register_buffer("SensorStream", buffer, batch_size=1000)
        # generate data in the main thread, publish via writer...

    # 4. Reading a .cbf file
    arr = cycflow.read_cbf_to_array("session.cbf")

    # 5. Dumping a RecBuffer to CSV or CBF
    cycflow.CsvWriter("out.csv", buffer)
    cycflow.CbfWriter("out.cbf", buffer)

    # 6. Async streaming with BufferClient notifications
    async for batch in cycflow.stream("127.0.0.1", 5000, "SensorStream"):
        ...
"""

from __future__ import annotations

from typing import AsyncIterator, Iterable
import asyncio

from ._cycflow import (  # type: ignore[attr-defined]
    # Enums / free functions
    DataType,
    get_type_size,
    data_type_to_string,
    data_type_from_string,
    get_current_epoch_time,
    # Core
    PAttr,
    PReg,
    BitRef,
    RecRule,
    Record,
    # Buffer
    BufferClient,
    RecBuffer,
    RecordBatch,
    RecordReader,
    # Writers / producers
    WriteBatch,
    RecordWriter,
    RecordProducer,
    BatchRecordProducer,
    # Consumers
    RecordConsumer,
    BatchRecordConsumer,
    CbfWriter,
    CsvWriter,
    # TCP client
    TcpServiceClient,
    TcpDataReceiver,
    # TCP server
    TcpServer,
    # CBF
    CbfReader,
    CbfFile,
    CbfMode,
    CbfSectionType,
    CbfSectionHeader,
    CBF_SECTION_MARKER,
    read_cbf_to_array,
    __version__,
)

__all__ = [
    # Core
    "DataType", "PAttr", "PReg", "BitRef", "RecRule", "Record",
    "get_type_size", "data_type_to_string", "data_type_from_string",
    "get_current_epoch_time",
    # Buffer
    "BufferClient", "RecBuffer", "RecordBatch", "RecordReader",
    # Writers / producers
    "WriteBatch", "RecordWriter", "RecordProducer", "BatchRecordProducer",
    # Consumers
    "RecordConsumer", "BatchRecordConsumer", "CbfWriter", "CsvWriter",
    # TCP
    "TcpServiceClient", "TcpDataReceiver", "TcpServer",
    # CBF
    "CbfReader", "CbfFile", "CbfMode", "CbfSectionType", "CbfSectionHeader",
    "CBF_SECTION_MARKER", "read_cbf_to_array",
    # Helpers
    "make_rule", "discover", "to_dataframe", "stream",
    "publish_dataframe",
    "__version__",
]


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def make_rule(attrs: Iterable, *, align: bool = False) -> RecRule:
    """
    Build a RecRule from a list of tuples or ready-made PAttr objects.

    Args:
        attrs: Iterable of PAttr objects or tuples. Tuples can be:
               - (name, DataType)
               - (name, DataType, count_or_bit_defs)
        align: When True, fields are stably sorted by decreasing element
               size for natural alignment, and trailing padding is appended
               so that packed record arrays stay aligned. Default False
               preserves the historical tightly-packed layout.

    Examples:
        cycflow.make_rule([("V", cycflow.DataType.Float)])
        cycflow.make_rule([("Flags", cycflow.DataType.UInt8, ["tx", "rx"])])
        cycflow.make_rule([("Arr", cycflow.DataType.Int16, 4)], align=True)
    """
    pattrs = []
    for a in attrs:
        if isinstance(a, PAttr):
            pattrs.append(a)
        elif len(a) == 2:
            name, t = a
            pattrs.append(PAttr(name, t))
        elif len(a) == 3:
            name, t, third = a
            if isinstance(third, (list, tuple)):
                pattrs.append(PAttr(name, t, [str(x) for x in third]))
            else:
                pattrs.append(PAttr(name, t, int(third)))
        else:
            raise ValueError(f"Unsupported attr spec: {a!r}")

    rule = RecRule()
    rule.init(pattrs, align)
    return rule


def discover(host: str, port: int) -> dict[str, RecRule]:
    """Return {buffer_name: RecRule} for all buffers published by the server."""
    out: dict[str, RecRule] = {}
    for name in TcpServiceClient.request_buffer_list(host, port):
        text = TcpServiceClient.request_rec_rule(host, port, name)
        if text:
            out[name] = RecRule.from_text(text)
    return out


# ---------------------------------------------------------------------------
# pandas interop
# ---------------------------------------------------------------------------

def to_dataframe(src):
    """
    Turn a numpy structured array or RecBuffer into a pandas.DataFrame.
    For CbfReader use read_cbf_to_array(path) first.
    """
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("pandas not installed") from e

    if isinstance(src, RecBuffer):
        arr = src.snapshot()
    elif isinstance(src, CbfReader):
        raise TypeError(
            "For CbfReader, use cycflow.read_cbf_to_array(path) -> ndarray."
        )
    elif hasattr(src, "snapshot"):
        arr = src.snapshot()
    else:
        arr = src  # assume np.ndarray
    return pd.DataFrame(arr)


def publish_dataframe(df, writer: RecordWriter) -> int:
    """
    Bulk-publish a pandas.DataFrame (or numpy structured array) into a
    RecordWriter. Assumes the column names and dtypes match the writer's
    RecRule. Returns the number of records actually committed.

    This is the fastest way to push precomputed data into a TCP server:
    the batch is filled via numpy memcpy, then committed in a single call.
    """
    import numpy as np

    # Accept DataFrames too.
    if hasattr(df, "to_records"):
        arr = df.to_records(index=False)
    else:
        arr = np.asarray(df)

    total = len(arr)
    if total == 0:
        return 0

    remaining = total
    pos = 0
    while remaining > 0:
        batch = writer.next_batch(remaining, wait=True)
        if not batch.is_valid():
            break
        view = batch.as_numpy()
        n = min(len(view), remaining)
        view[:n] = arr[pos : pos + n]
        writer.commit_batch(n)
        pos += n
        remaining -= n
    return pos


# ---------------------------------------------------------------------------
# Asyncio streaming (client)
# ---------------------------------------------------------------------------

async def stream(
    host: str,
    port: int,
    buffer_name: str,
    *,
    buffer_capacity: int = 65536,
    batch_capacity: int = 1000,
    copy: bool = True,
) -> AsyncIterator:
    """
    Async generator that yields numpy arrays from a CycFlow TCP stream.
    See the module docstring for a usage example.
    """
    loop = asyncio.get_running_loop()
    event = asyncio.Event()

    def _on_data() -> None:
        loop.call_soon_threadsafe(event.set)

    client = BufferClient(_on_data)
    rx = TcpDataReceiver(buffer_capacity=buffer_capacity)

    ok = await loop.run_in_executor(
        None, rx.connect, host, port, buffer_name
    )
    if not ok:
        raise ConnectionError(f"Failed to connect to {host}:{port}/{buffer_name}")

    buf = rx.get_buffer()
    buf.add_client(client)

    reader = RecordReader(buf, batch_capacity=batch_capacity)
    next_batch = reader.next_batch_copy if copy else reader.next_batch

    try:
        while rx.is_running():
            while True:
                b = next_batch(batch_capacity, False)
                if b is None:
                    break
                yield b
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
    finally:
        reader.stop()
        rx.stop()
        buf.remove_client(client)
