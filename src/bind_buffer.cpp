//
// bind_buffer.cpp — IRecBufferClient (trampoline), RecBuffer, RecordReader.
//
// Key point: RecordReader::nextBatch() returns a contiguous block of batch
// memory — that's where the zero-copy numpy view is built.
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
#include "RecordReader.h"

#include "numpy_bridge.h"

namespace py = pybind11;

namespace cyclibpy {

// ---------------------------------------------------------------------------
// PyBufferClient — a RecBuffer subscriber that dispatches to a Python callback.
//
// Replaces the setOnData facility that does not exist in CycLib: created
// from Python, registered via RecBuffer.add_client(), and invokes the user
// callback on every notification. The callback runs on the writer thread,
// so we acquire the GIL before entering Python.
// ---------------------------------------------------------------------------
class PyBufferClient : public cyc::IRecBufferClient {
public:
    explicit PyBufferClient(py::function cb)
        : m_cb(std::move(cb)), m_cursor(UINT64_MAX) {}

    // IRecBufferClient ------------------------------------------------------
    void notifyDataAvailable() override {
        py::gil_scoped_acquire gil;
        try {
            m_cb();
        } catch (py::error_already_set& e) {
            // Never let a Python exception kill the writer thread.
            e.discard_as_unraisable(__func__);
        }
    }

    uint64_t getCursor() const override {
        return m_cursor.load(std::memory_order_acquire);
    }

    // Python-facing helpers -------------------------------------------------
    void set_cursor(uint64_t c) {
        m_cursor.store(c, std::memory_order_release);
    }

private:
    py::function m_cb;
    std::atomic<uint64_t> m_cursor;
};


void bind_buffer(py::module_& m) {
    // ----- PyBufferClient (user-facing subscription) -------------------------
    py::class_<PyBufferClient>(m, "BufferClient",
        "RecBuffer subscriber. The callback is invoked on the writer thread.")
        .def(py::init<py::function>(), py::arg("callback"),
             "callback() — invoked every time new data is pushed.")
        .def("set_cursor", &PyBufferClient::set_cursor, py::arg("cursor"),
             "Set the client's read cursor. UINT64_MAX (default) marks the "
             "client as passive — it will not block the writer (useful for "
             "UI/monitoring clients).");

    // ----- RecBuffer ---------------------------------------------------------
    // Holder is shared_ptr because receivers/producers hand the buffer out
    // that way. addClient/removeClient take raw pointers; we use keep_alive
    // to ensure the Python-owned client object isn't GC'd while registered.
    py::class_<cyc::RecBuffer, std::shared_ptr<cyc::RecBuffer>>(m, "RecBuffer")
        .def(py::init<const cyc::RecRule&, std::size_t>(),
             py::arg("rule"), py::arg("capacity"))

        .def("get_rule",     &cyc::RecBuffer::getRule,
             py::return_value_policy::reference_internal)
        .def("get_rec_size", &cyc::RecBuffer::getRecSize)
        .def("size",         &cyc::RecBuffer::size)
        .def("capacity",     &cyc::RecBuffer::capacity)
        .def("get_total_written",          &cyc::RecBuffer::getTotalWritten)
        .def("get_total_written_and_size", &cyc::RecBuffer::getTotalWrittenAndSize)
        .def("get_available_write_space",  &cyc::RecBuffer::getAvailableWriteSpace)
        .def("notify_writers",             &cyc::RecBuffer::notifyWriters)

        // Client subscription. keep_alive binds the client's lifetime to the
        // buffer so the Python object is not collected while registered.
        .def("add_client",
             [](cyc::RecBuffer& buf, PyBufferClient* c) { buf.addClient(c); },
             py::arg("client"),
             py::keep_alive<1, 2>())
        .def("remove_client",
             [](cyc::RecBuffer& buf, PyBufferClient* c) { buf.removeClient(c); },
             py::arg("client"))

        // snapshot — a safe copy of current contents into a numpy array.
        // Uses readRelative() on top of DynamicChunkBuffer — locked internally.
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

    // ----- RecordReader::RecordBatch -----------------------------------------
    // Wrapper around a batch's contiguous memory block. The block remains
    // valid only until the next nextBatch() call on the same reader.
    py::class_<cyc::RecordReader::RecordBatch>(m, "RecordBatch",
        "Contiguous memory block of a batch (view, not a copy)")
        .def_readonly("count",       &cyc::RecordReader::RecordBatch::count)
        .def_readonly("record_size", &cyc::RecordReader::RecordBatch::recordSize)
        .def("is_valid",             &cyc::RecordReader::RecordBatch::isValid)
        .def_property_readonly("rule",
            [](const cyc::RecordReader::RecordBatch& b) -> const cyc::RecRule& {
                return b.rule;
            },
            py::return_value_policy::reference);

    // ----- RecordReader ------------------------------------------------------
    py::class_<cyc::RecordReader>(m, "RecordReader",
        "Asynchronous reader on top of RecBuffer with double-buffered prefetch")
        .def(py::init<std::shared_ptr<cyc::RecBuffer>, std::size_t>(),
             py::arg("buffer"), py::arg("batch_capacity") = 1000)

        .def("stop",   &cyc::RecordReader::stop,
             py::call_guard<py::gil_scoped_release>())
        .def("finish", &cyc::RecordReader::finish,
             py::call_guard<py::gil_scoped_release>())
        .def("get_rule", &cyc::RecordReader::getRule,
             py::return_value_policy::reference_internal)
        .def("get_cursor", &cyc::RecordReader::getCursor)

        // next_record — returns a Record that references the reader's RecRule,
        // so keep_alive<0, 1> ties the Record to this reader.
        .def("next_record",
             [](cyc::RecordReader& r) { return r.nextRecord(); },
             py::call_guard<py::gil_scoped_release>(),
             py::keep_alive<0, 1>())

        // next_batch — the main method for high-throughput consumption.
        // Returns a numpy view with owner=reader so the ndarray keeps it alive.
        // IMPORTANT: the array is valid only until the next next_batch call on
        // the SAME reader. Use next_batch_copy or .copy() if you need to
        // retain the data across calls.
        .def("next_batch",
            [](py::object self, std::size_t max_records, bool wait) -> py::object {
                auto& r = self.cast<cyc::RecordReader&>();
                cyc::RecordReader::RecordBatch batch = [&] {
                    py::gil_scoped_release release;
                    return r.nextBatch(max_records, wait);
                }();
                if (!batch.isValid()) return py::none();
                return view_structured(batch.rule, batch.data, batch.count, self);
            },
            py::arg("max_records"), py::arg("wait") = true,
            "Zero-copy view of the next batch as a numpy structured array. "
            "The view is INVALIDATED by the next next_batch() call on the same "
            "reader — call .copy() or use next_batch_copy() if you need to keep it.")

        // next_batch_copy — safe copy (nothing gets invalidated).
        .def("next_batch_copy",
            [](cyc::RecordReader& r, std::size_t max_records, bool wait) -> py::object {
                cyc::RecordReader::RecordBatch batch = [&] {
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
            },
            py::arg("max_records"), py::arg("wait") = true);
}

} // namespace cyclibpy
