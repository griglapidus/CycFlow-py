//
// bind_writer.cpp — writer hierarchy (RecordWriterBase / RecordWriter /
// RecordWriterZC) and the producer hierarchy (RecordProducer /
// BatchRecordProducer).
//
// v0.4.0: writer hierarchy split into an abstract RecordWriterBase and two
// concrete implementations:
//   - RecordWriter   — double-buffered async (existing semantics)
//   - RecordWriterZC — zero-copy, synchronous (new, single-producer only)
// Common methods are bound on the base so RecordProducer::getWriter() (which
// now returns RecordWriterBase&) can drive any writer transparently.
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <pybind11/functional.h>

#include <cstring>

#include "Core/RecBuffer.h"
#include "Core/Record.h"
#include "Core/RecRule.h"
#include "RecordWriterBase.h"
#include "RecordWriter.h"
#include "RecordWriterZC.h"
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
            cyc::RecRule, cyc::RecordProducer, "define_rule", defineRule);
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
    // ----- WriteBatch (writable view) ----------------------------------------
    // The struct moved to RecordWriterBase in v0.4.0; RecordWriter::RecordBatch
    // and RecordWriterZC::RecordBatch are typedefs of this single type.
    py::class_<cyc::RecordWriterBase::RecordBatch>(m, "WriteBatch",
        "Writable contiguous block of record memory returned by "
        "RecordWriter.next_batch() / RecordWriterZC.next_batch().")
        .def_readonly("capacity",    &cyc::RecordWriterBase::RecordBatch::capacity)
        .def_readonly("record_size", &cyc::RecordWriterBase::RecordBatch::recordSize)
        .def("is_valid",             &cyc::RecordWriterBase::RecordBatch::isValid)
        .def_property_readonly("rule",
            [](const cyc::RecordWriterBase::RecordBatch& b) -> const cyc::RecRule& {
                return b.rule;
            },
            py::return_value_policy::reference)
        .def("as_numpy",
            [](py::object self) {
                auto& b = self.cast<cyc::RecordWriterBase::RecordBatch&>();
                return view_structured(b.rule, b.data, b.capacity, self);
            },
            "Zero-copy writable numpy structured array view. "
            "Fill it in Python, then call writer.commit_batch(count).");

    // ----- RecordWriterBase (abstract) ---------------------------------------
    py::class_<cyc::RecordWriterBase>(m, "RecordWriterBase",
        "Abstract base for RecBuffer writers. Common methods "
        "(next_record/commit_record, next_batch/commit_batch, flush, stop, "
        "get_rule) live here.")
        // GIL is released on every call that can block on backpressure
        // (RecordWriterZC::commitRecord/commitBatch wait for ring-buffer
        // space when block_on_full=true; RecordWriter::next* may stall
        // when the active buffer fills before the worker swaps).
        .def("next_record",
             [](cyc::RecordWriterBase& w) { return w.nextRecord(); },
             py::call_guard<py::gil_scoped_release>(),
             py::keep_alive<0, 1>(),
             "Acquire the next record slot. Fill it, then call commit_record().")
        .def("commit_record", &cyc::RecordWriterBase::commitRecord,
             py::call_guard<py::gil_scoped_release>(),
             "Commit the record previously acquired via next_record().")

        .def("next_batch",
             [](cyc::RecordWriterBase& w, std::size_t max_records, bool wait) {
                 return w.nextBatch(max_records, wait);
             },
             py::arg("max_records"), py::arg("wait") = true,
             py::call_guard<py::gil_scoped_release>(),
             "Acquire up to max_records slots for bulk writing.")
        .def("commit_batch", &cyc::RecordWriterBase::commitBatch, py::arg("count"),
             py::call_guard<py::gil_scoped_release>(),
             "Commit `count` records written to the active batch.")

        .def("flush", &cyc::RecordWriterBase::flush,
             py::call_guard<py::gil_scoped_release>(),
             "Flush pending data. For RecordWriterZC this is a no-op.")
        .def("stop", &cyc::RecordWriterBase::stop,
             py::call_guard<py::gil_scoped_release>(),
             "Unblock any thread waiting on backpressure. Required for safe "
             "shutdown of RecordWriterZC when block_on_full=True; no-op for "
             "RecordWriter (its destructor handles shutdown).")
        .def("get_rule", &cyc::RecordWriterBase::getRule,
             py::return_value_policy::reference_internal);

    // ----- RecordWriter (double-buffered async) ------------------------------
    py::class_<cyc::RecordWriter, cyc::RecordWriterBase>(m, "RecordWriter",
        "Asynchronous writer that pushes records into a RecBuffer with a "
        "double-buffering strategy.")
        .def(py::init<>(),
             "Default constructor — call init() before use.")
        .def(py::init<std::shared_ptr<cyc::RecBuffer>, std::size_t, bool>(),
             py::arg("buffer"), py::arg("batch_capacity"),
             py::arg("block_on_full") = true)
        .def("init", &cyc::RecordWriter::init,
             py::arg("buffer"), py::arg("batch_capacity"),
             py::arg("block_on_full") = true,
             "Initialise a default-constructed writer.");

    // ----- RecordWriterZC (zero-copy, synchronous) ---------------------------
    py::class_<cyc::RecordWriterZC, cyc::RecordWriterBase>(m, "RecordWriterZC",
        "Synchronous, single-buffered writer. Single-producer only — must be "
        "the sole writer attached to its target RecBuffer. Lower latency and "
        "no worker thread, but the caller's thread stalls when the buffer is "
        "full. Call stop() before joining the producer thread to avoid "
        "deadlocks when readers stop advancing.")
        .def(py::init<>(),
             "Default constructor — call init() before use.")
        .def(py::init<std::shared_ptr<cyc::RecBuffer>, std::size_t, bool>(),
             py::arg("buffer"), py::arg("batch_capacity"),
             py::arg("block_on_full") = true)
        .def("init", &cyc::RecordWriterZC::init,
             py::arg("buffer"), py::arg("batch_capacity"),
             py::arg("block_on_full") = true,
             "Initialise a default-constructed writer.")
        // RecordWriterZC::stop() is declared without `override` but still
        // shadows the base virtual. Bind it explicitly so Python users get
        // the derived version regardless.
        .def("stop", &cyc::RecordWriterZC::stop,
             py::call_guard<py::gil_scoped_release>(),
             "Unblock any commit call waiting for ring-buffer space.");

    // ----- RecordProducer ----------------------------------------------------
    py::class_<cyc::RecordProducer, PyRecordProducer>(m, "RecordProducer",
        "Abstract producer base class. Subclass in Python and override "
        "define_rule() and produce_step(rec).")
        .def(py::init<std::size_t, std::size_t>(),
             py::arg("buffer_capacity")   = 10000,
             py::arg("writer_batch_size") = 100)
        // init() / init_zc() let Python pick the writer type before start().
        .def("init",
            [](cyc::RecordProducer& self,
               std::size_t cap, std::size_t batch) {
                self.init(cap, batch);
            },
            py::arg("buffer_capacity")   = 10000,
            py::arg("writer_batch_size") = 100,
            "Re-configure to use the default RecordWriter (double-buffered).")
        .def("init_zc",
            [](cyc::RecordProducer& self,
               std::size_t cap, std::size_t batch) {
                self.init(cyc::UseWriter<cyc::RecordWriterZC>{}, cap, batch);
            },
            py::arg("buffer_capacity")   = 10000,
            py::arg("writer_batch_size") = 100,
            "Re-configure to use RecordWriterZC (zero-copy, synchronous). "
            "Must be the only writer on its buffer.")
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
