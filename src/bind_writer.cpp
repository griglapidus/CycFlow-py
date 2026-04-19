//
// bind_writer.cpp — RecordWriter, RecordProducer, BatchRecordProducer.
//
// Writers are the mirror of readers: they push records into a RecBuffer.
// RecordProducer is an abstract class that users typically subclass; we expose
// it via a pybind11 trampoline so Python classes can override define_rule()
// and produce_step().
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <pybind11/functional.h>

#include <cstring>

#include "Core/RecBuffer.h"
#include "Core/Record.h"
#include "Core/RecRule.h"
#include "RecordWriter.h"
#include "RecordProducer.h"

#include "numpy_bridge.h"

namespace py = pybind11;

namespace cyclibpy {

// ---------------------------------------------------------------------------
// Trampolines — allow Python subclasses to override virtual methods.
// ---------------------------------------------------------------------------
class PyRecordProducer : public cyc::RecordProducer {
public:
    using cyc::RecordProducer::RecordProducer;

    cyc::RecRule defineRule() override {
        PYBIND11_OVERRIDE_PURE_NAME(
            cyc::RecRule,         // return type
            cyc::RecordProducer,  // parent class
            "define_rule",        // name in Python
            defineRule            // name in C++
        );
    }

    bool produceStep(cyc::Record& rec) override {
        PYBIND11_OVERRIDE_PURE_NAME(
            bool, cyc::RecordProducer, "produce_step", produceStep, rec);
    }

    void onProduceStart() override {
        PYBIND11_OVERRIDE_NAME(
            void, cyc::RecordProducer, "on_produce_start", onProduceStart);
    }

    void onProduceStop() override {
        PYBIND11_OVERRIDE_NAME(
            void, cyc::RecordProducer, "on_produce_stop", onProduceStop);
    }
};

class PyBatchRecordProducer : public cyc::BatchRecordProducer {
public:
    using cyc::BatchRecordProducer::BatchRecordProducer;

    cyc::RecRule defineRule() override {
        PYBIND11_OVERRIDE_PURE_NAME(
            cyc::RecRule, cyc::BatchRecordProducer, "define_rule", defineRule);
    }

    size_t produceBatch(const cyc::RecordWriter::RecordBatch& batch) override {
        PYBIND11_OVERRIDE_PURE_NAME(
            size_t, cyc::BatchRecordProducer, "produce_batch", produceBatch, batch);
    }

    void onProduceStart() override {
        PYBIND11_OVERRIDE_NAME(
            void, cyc::BatchRecordProducer, "on_produce_start", onProduceStart);
    }

    void onProduceStop() override {
        PYBIND11_OVERRIDE_NAME(
            void, cyc::BatchRecordProducer, "on_produce_stop", onProduceStop);
    }
};


void bind_writer(py::module_& m) {
    // ----- RecordWriter::RecordBatch (writable view) -------------------------
    py::class_<cyc::RecordWriter::RecordBatch>(m, "WriteBatch",
        "Writable contiguous block of record memory returned by "
        "RecordWriter.next_batch().")
        .def_readonly("capacity",    &cyc::RecordWriter::RecordBatch::capacity)
        .def_readonly("record_size", &cyc::RecordWriter::RecordBatch::recordSize)
        .def("is_valid",             &cyc::RecordWriter::RecordBatch::isValid)
        .def_property_readonly("rule",
            [](const cyc::RecordWriter::RecordBatch& b) -> const cyc::RecRule& {
                return b.rule;
            },
            py::return_value_policy::reference)
        // Zero-copy writable numpy view over the batch memory. The view is
        // valid until commit_batch() is called on the parent writer.
        .def("as_numpy",
            [](py::object self) {
                auto& b = self.cast<cyc::RecordWriter::RecordBatch&>();
                return view_structured(b.rule, b.data, b.capacity, self);
            },
            "Zero-copy writable numpy structured array view. "
            "Fill it in Python, then call writer.commit_batch(count).");

    // ----- RecordWriter ------------------------------------------------------
    py::class_<cyc::RecordWriter>(m, "RecordWriter",
        "Asynchronous writer that pushes records into a RecBuffer "
        "with a double-buffering strategy.")
        .def(py::init<std::shared_ptr<cyc::RecBuffer>, std::size_t, bool>(),
             py::arg("buffer"), py::arg("batch_capacity"),
             py::arg("block_on_full") = true)

        // Single-record API.
        .def("next_record",
             [](cyc::RecordWriter& w) { return w.nextRecord(); },
             py::keep_alive<0, 1>(),
             "Acquire the next record slot. Fill it, then call commit_record().")
        .def("commit_record", &cyc::RecordWriter::commitRecord,
             "Commit the record previously acquired via next_record().")

        // Batch API.
        .def("next_batch",
             [](cyc::RecordWriter& w, std::size_t max_records, bool wait) {
                 return w.nextBatch(max_records, wait);
             },
             py::arg("max_records"), py::arg("wait") = true,
             py::call_guard<py::gil_scoped_release>(),
             "Acquire up to max_records slots for bulk writing.")
        .def("commit_batch", &cyc::RecordWriter::commitBatch, py::arg("count"),
             "Commit `count` records written to the active batch.")

        // Control.
        .def("flush", &cyc::RecordWriter::flush,
             py::call_guard<py::gil_scoped_release>(),
             "Flush all pending data and block until the worker finishes.");

    // ----- RecordProducer ----------------------------------------------------
    py::class_<cyc::RecordProducer, PyRecordProducer>(m, "RecordProducer",
        "Abstract producer base class. Subclass in Python and override "
        "define_rule() and produce_step(rec).")
        .def(py::init<std::size_t, std::size_t>(),
             py::arg("buffer_capacity")   = 10000,
             py::arg("writer_batch_size") = 100)
        .def("start",      &cyc::RecordProducer::start,
             py::call_guard<py::gil_scoped_release>())
        .def("stop",       &cyc::RecordProducer::stop,
             py::call_guard<py::gil_scoped_release>())
        .def("join",       &cyc::RecordProducer::join,
             py::call_guard<py::gil_scoped_release>())
        .def("is_running", &cyc::RecordProducer::isRunning)
        .def("get_buffer", &cyc::RecordProducer::getBuffer)
        .def("get_writer", &cyc::RecordProducer::getWriter,
             py::return_value_policy::reference_internal);

    // ----- BatchRecordProducer ----------------------------------------------
    py::class_<cyc::BatchRecordProducer, cyc::RecordProducer,
               PyBatchRecordProducer>(m, "BatchRecordProducer",
        "Optimised producer base class for bulk generation. Subclass and "
        "override define_rule() and produce_batch(batch).")
        .def(py::init<std::size_t, std::size_t>(),
             py::arg("buffer_capacity")   = 10000,
             py::arg("writer_batch_size") = 1000);
}

} // namespace cyclibpy
