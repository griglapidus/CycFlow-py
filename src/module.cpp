//
// module.cpp — Python module entry point for _cyclib.
//

#include <pybind11/pybind11.h>

namespace py = pybind11;

namespace cyclibpy {
    void bind_core(py::module_&);
    void bind_buffer(py::module_&);
    void bind_writer(py::module_&);
    void bind_consumer(py::module_&);
    void bind_tcp(py::module_&);
    void bind_server(py::module_&);
    void bind_cbf(py::module_&);
}

PYBIND11_MODULE(_cyclib, m) {
    m.doc() = "Python bindings for CycFlow / CycLib";

    cyclibpy::bind_core(m);     // DataType, PAttr, PReg, BitRef, RecRule, Record
    cyclibpy::bind_buffer(m);   // BufferClient, RecBuffer, RecordBatch, RecordReader
    cyclibpy::bind_writer(m);   // WriteBatch, RecordWriter, RecordProducer, BatchRecordProducer
    cyclibpy::bind_consumer(m); // RecordConsumer, BatchRecordConsumer, CbfWriter, CsvWriter
    cyclibpy::bind_tcp(m);      // TcpServiceClient, TcpDataReceiver
    cyclibpy::bind_server(m);   // TcpServer
    cyclibpy::bind_cbf(m);      // CbfFile, CbfReader, read_cbf_to_array, CBF enums

    m.attr("__version__") = "0.2.0";
}
