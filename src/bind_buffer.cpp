//
// bind_buffer.cpp — IRecBufferClient (trampoline), RecBuffer and the reader
// hierarchy (RecordReaderBase / RecordReader / RecordReaderZC).
//
// v0.4.0: the reader hierarchy was split into an abstract RecordReaderBase
// and two concrete implementations:
//   - RecordReader   — double-buffered async (existing semantics)
//   - RecordReaderZC — zero-copy, synchronous (new)
// All common methods are bound on the base so polymorphic returns from
// RecordConsumer::getReader() and similar APIs work transparently.
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <pybind11/functional.h>

#include <functional>
#include <memory>
#include <mutex>
#include <cstdint>
#include <cstring>

#include "Core/RecBuffer.h"
#include "Core/Record.h"
#include "Core/IRecBufferClient.h"
#include "RecordReaderBase.h"
#include "RecordReader.h"
#include "RecordReaderZC.h"

#include "numpy_bridge.h"

namespace py = pybind11;

namespace cyclibpy {

// ---------------------------------------------------------------------------
// PyBufferClient — a RecBuffer subscriber that dispatches to a Python callback.
// ---------------------------------------------------------------------------
class PyBufferClient : public cyc::IRecBufferClient {
public:
    explicit PyBufferClient(py::function cb)
        : m_cb(std::move(cb)), m_cursor(UINT64_MAX) {}

    void notifyDataAvailable() override {
        py::gil_scoped_acquire gil;
        try {
            m_cb();
        } catch (py::error_already_set& e) {
            e.discard_as_unraisable(__func__);
        }
    }

    uint64_t getCursor() const override {
        return m_cursor.load(std::memory_order_acquire);
    }

    void set_cursor(uint64_t c) {
        m_cursor.store(c, std::memory_order_release);
    }

private:
    py::function m_cb;
    std::atomic<uint64_t> m_cursor;
};


// ---------------------------------------------------------------------------
// Helpers shared by both reader bindings.
// ---------------------------------------------------------------------------
static py::object reader_next_batch_view(py::object self,
                                         std::size_t max_records,
                                         bool wait) {
    auto& r = self.cast<cyc::RecordReaderBase&>();
    cyc::RecordReaderBase::RecordBatch batch = [&] {
        py::gil_scoped_release release;
        return r.nextBatch(max_records, wait);
    }();
    if (!batch.isValid()) return py::none();
    return view_structured(batch.rule, batch.data, batch.count, self);
}

static py::object reader_next_batch_copy(cyc::RecordReaderBase& r,
                                         std::size_t max_records,
                                         bool wait) {
    cyc::RecordReaderBase::RecordBatch batch = [&] {
        py::gil_scoped_release release;
        return r.nextBatch(max_records, wait);
    }();
    if (!batch.isValid()) return py::none();
    py::module_ np = py::module_::import("numpy");
    py::object dtype = dtype_from_rule(batch.rule);
    py::array arr = np.attr("empty")(batch.count, dtype);
    std::memcpy(arr.mutable_data(), batch.data,
                batch.count * batch.recordSize);
    return std::move(arr);
}


void bind_buffer(py::module_& m) {
    // ----- PyBufferClient ----------------------------------------------------
    py::class_<PyBufferClient>(m, "BufferClient",
        "RecBuffer subscriber. The callback is invoked on the writer thread.")
        .def(py::init<py::function>(), py::arg("callback"),
             "callback() — invoked every time new data is pushed.")
        .def("set_cursor", &PyBufferClient::set_cursor, py::arg("cursor"),
             "Set the client's read cursor. UINT64_MAX (default) marks the "
             "client as passive — it will not block the writer.");

    // ----- RecBuffer ---------------------------------------------------------
    py::class_<cyc::RecBuffer, std::shared_ptr<cyc::RecBuffer>>(m, "RecBuffer")
        .def(py::init<>(),
             "Default constructor — buffer is uninitialised, call init() before use.")
        .def(py::init<const cyc::RecRule&, std::size_t>(),
             py::arg("rule"), py::arg("capacity"))
        .def("init", &cyc::RecBuffer::init,
             py::arg("rule"), py::arg("capacity"),
             "Initialise a default-constructed buffer with a schema and capacity.")

        .def("get_rule",     &cyc::RecBuffer::getRule,
             py::return_value_policy::reference_internal)
        .def("get_rec_size", &cyc::RecBuffer::getRecSize)
        .def("size",         &cyc::RecBuffer::size)
        .def("capacity",     &cyc::RecBuffer::capacity)
        .def("get_total_written",          &cyc::RecBuffer::getTotalWritten)
        .def("get_total_written_and_size", &cyc::RecBuffer::getTotalWrittenAndSize)
        .def("get_available_write_space",  &cyc::RecBuffer::getAvailableWriteSpace)
        .def("notify_writers",             &cyc::RecBuffer::notifyWriters)

        .def("add_client",
             [](cyc::RecBuffer& buf, PyBufferClient* c) { buf.addClient(c); },
             py::arg("client"),
             py::keep_alive<1, 2>())
        .def("remove_client",
             [](cyc::RecBuffer& buf, PyBufferClient* c) { buf.removeClient(c); },
             py::arg("client"))

        .def("snapshot",
            [](cyc::RecBuffer& buf) {
                const auto& rule = buf.getRule();
                const std::size_t n = buf.size();
                py::module_ np = py::module_::import("numpy");
                py::object dtype = dtype_from_rule(rule);
                py::array arr = np.attr("empty")(n, dtype);
                if (n > 0) {
                    py::gil_scoped_release release;
                    buf.readRelative(0, arr.mutable_data(), n);
                }
                return arr;
            },
            "Copy of all valid records into a new numpy structured array.");

    // ----- RecordBatch (read-only view) --------------------------------------
    // The struct moved to RecordReaderBase in v0.4.0; existing
    // RecordReader::RecordBatch / RecordReaderZC::RecordBatch are typedefs of
    // this single type — bind it once.
    py::class_<cyc::RecordReaderBase::RecordBatch>(m, "RecordBatch",
        "Contiguous memory block of a batch (view, not a copy). "
        "For RecordReader the view is valid until the next next_batch(). "
        "For RecordReaderZC the view is valid only until the next "
        "next_batch() or release() — see the class docs.")
        .def_readonly("count",       &cyc::RecordReaderBase::RecordBatch::count)
        .def_readonly("record_size", &cyc::RecordReaderBase::RecordBatch::recordSize)
        .def("is_valid",             &cyc::RecordReaderBase::RecordBatch::isValid)
        .def_property_readonly("rule",
            [](const cyc::RecordReaderBase::RecordBatch& b) -> const cyc::RecRule& {
                return b.rule;
            },
            py::return_value_policy::reference);

    // ----- RecordReaderBase (abstract) ---------------------------------------
    // Bound first so RecordConsumer.get_reader() (and any polymorphic API
    // that returns RecordReaderBase) can be downcast correctly in Python.
    py::class_<cyc::RecordReaderBase>(m, "RecordReaderBase",
        "Abstract base for RecBuffer readers. Common methods (stop, finish, "
        "get_rule, get_cursor, next_record, next_batch, release) live here.")
        .def("stop",   &cyc::RecordReaderBase::stop,
             py::call_guard<py::gil_scoped_release>())
        .def("finish", &cyc::RecordReaderBase::finish,
             py::call_guard<py::gil_scoped_release>())
        .def("release", &cyc::RecordReaderBase::release,
             py::call_guard<py::gil_scoped_release>(),
             "Unpin the current batch. No-op for RecordReader; advances the "
             "cursor for RecordReaderZC so the writer may reuse the region.")
        .def("get_rule",   &cyc::RecordReaderBase::getRule,
             py::return_value_policy::reference_internal)
        .def("get_cursor", &cyc::RecordReaderBase::getCursor)

        .def("next_record",
             [](cyc::RecordReaderBase& r) { return r.nextRecord(); },
             py::call_guard<py::gil_scoped_release>(),
             py::keep_alive<0, 1>())

        .def("next_batch",       &reader_next_batch_view,
             py::arg("max_records"), py::arg("wait") = true,
             "Zero-copy view of the next batch as a numpy structured array. "
             "The view is INVALIDATED by the next next_batch() call on the same "
             "reader — call .copy() or use next_batch_copy() if you need to keep it.")
        .def("next_batch_copy",  &reader_next_batch_copy,
             py::arg("max_records"), py::arg("wait") = true,
             "Safe copy of the next batch into a fresh numpy array.");

    // ----- RecordReader (double-buffered async) ------------------------------
    py::class_<cyc::RecordReader, cyc::RecordReaderBase>(m, "RecordReader",
        "Asynchronous reader on top of RecBuffer with double-buffered prefetch.")
        .def(py::init<>(),
             "Default constructor — call init() before use.")
        .def(py::init<std::shared_ptr<cyc::RecBuffer>, std::size_t>(),
             py::arg("buffer"), py::arg("batch_capacity") = 1000)
        .def("init", &cyc::RecordReader::init,
             py::arg("buffer"), py::arg("batch_capacity"),
             "Initialise a default-constructed reader.");

    // ----- RecordReaderZC (zero-copy, synchronous) ---------------------------
    py::class_<cyc::RecordReaderZC, cyc::RecordReaderBase>(m, "RecordReaderZC",
        "Zero-copy, synchronous reader. nextBatch() returns a pointer directly "
        "into the ring buffer; the view is valid only until the next "
        "next_batch() or release() call. Backpressure: a slow reader stalls "
        "the writer because the reader cursor only advances on release().")
        .def(py::init<>(),
             "Default constructor — call init() before use.")
        .def(py::init<std::shared_ptr<cyc::RecBuffer>, std::size_t>(),
             py::arg("buffer"), py::arg("batch_capacity") = 1000)
        .def("init", &cyc::RecordReaderZC::init,
             py::arg("buffer"), py::arg("batch_capacity"),
             "Initialise a default-constructed reader.");
}

} // namespace cyclibpy
