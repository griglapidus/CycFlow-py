//
// bind_tcp.cpp — TcpDataReceiver + TcpServiceClient.
//
// v0.4.0: TcpDataReceiver now accepts a UseWriter<WriterType> tag in its
// template constructor to pick the writer implementation. Expose this from
// Python via a `use_zc` boolean argument.
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "Tcp/TcpDataReceiver.h"
#include "Tcp/TcpServiceClient.h"
#include "RecordWriter.h"
#include "RecordWriterZC.h"
#include "RecordProducer.h"

namespace py = pybind11;

namespace cyclibpy {

void bind_tcp(py::module_& m) {
    // ----- TcpServiceClient --------------------------------------------------
    py::class_<cyc::TcpServiceClient>(m, "TcpServiceClient",
        "Synchronous queries against a CycFlow server: buffer list and schema.")
        .def_static("request_buffer_list",
            &cyc::TcpServiceClient::requestBufferList,
            py::arg("host"), py::arg("port"),
            py::call_guard<py::gil_scoped_release>(),
            "Return the list of buffer names published by the server.")
        .def_static("request_rec_rule",
            &cyc::TcpServiceClient::requestRecRule,
            py::arg("host"), py::arg("port"), py::arg("buffer_name"),
            py::call_guard<py::gil_scoped_release>(),
            "Return the buffer's schema as text "
            "(consumable by RecRule.from_text()).");

    // ----- TcpDataReceiver ---------------------------------------------------
    py::class_<cyc::TcpDataReceiver>(m, "TcpDataReceiver",
        "CycFlow TCP client. After connect() it automatically negotiates the "
        "schema and fills the local RecBuffer in a background thread.")

        .def(py::init(
            [](std::size_t cap, std::size_t batch, bool use_zc) {
                return use_zc
                    ? new cyc::TcpDataReceiver(cyc::UseWriter<cyc::RecordWriterZC>{},
                                               cap, batch)
                    : new cyc::TcpDataReceiver(cap, batch);
            }),
            py::arg("buffer_capacity")   = 65536,
            py::arg("writer_batch_size") = 1000,
            py::arg("use_zc")            = false,
            "use_zc=True picks RecordWriterZC for the local buffer; "
            "single-producer constraint applies.")

        .def("connect",      &cyc::TcpDataReceiver::connect,
             py::arg("host"), py::arg("port"), py::arg("buffer_name"),
             py::call_guard<py::gil_scoped_release>(),
             "Connect, perform handshake, and start the background receive "
             "loop. Returns True on success.")
        .def("stop",         &cyc::TcpDataReceiver::stop,
             py::call_guard<py::gil_scoped_release>())
        .def("is_connected", &cyc::TcpDataReceiver::isConnected)

        // Inherited from RecordProducer.
        .def("start",      &cyc::RecordProducer::start,
             py::call_guard<py::gil_scoped_release>())
        .def("join",       &cyc::RecordProducer::join,
             py::call_guard<py::gil_scoped_release>())
        .def("is_running", &cyc::RecordProducer::isRunning)
        .def("get_buffer", &cyc::RecordProducer::getBuffer,
             "shared_ptr<RecBuffer> — the local buffer being filled")

        .def("__enter__", [](py::object self) { return self; })
        .def("__exit__",  [](cyc::TcpDataReceiver& r,
                             py::object, py::object, py::object) {
            r.stop();
            return false;
        });
}

} // namespace cyclibpy
