//
// bind_consumer.cpp — RecordConsumer hierarchy and the concrete file writers
// (CbfWriter, CsvWriter).
//
// v0.4.0: RecordConsumer now manages a RecordReaderBase-derived reader
// (RecordReader or RecordReaderZC) chosen via init<T>(). Python exposes both
// via .init() and .init_zc() methods. CbfWriter/CsvWriter accept use_zc
// constructor flag to pick the reader variant.
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "Core/RecBuffer.h"
#include "RecordReaderBase.h"
#include "RecordReader.h"
#include "RecordReaderZC.h"
#include "RecordConsumer.h"
#include "Cbf/CbfWriter.h"
#include "Csv/CsvWriter.h"

namespace py = pybind11;

namespace cyclibpy {

// ---------------------------------------------------------------------------
// Trampolines for Python subclasses.
// ---------------------------------------------------------------------------
class PyRecordConsumer : public cyc::RecordConsumer {
public:
    using cyc::RecordConsumer::RecordConsumer;

    void consumeRecord(const cyc::Record& rec) override {
        PYBIND11_OVERRIDE_PURE_NAME(
            void, cyc::RecordConsumer, "consume_record", consumeRecord, rec);
    }

    void onConsumeStart() override {
        PYBIND11_OVERRIDE_NAME(
            void, cyc::RecordConsumer, "on_consume_start", onConsumeStart);
    }

    void onConsumeStop() override {
        PYBIND11_OVERRIDE_NAME(
            void, cyc::RecordConsumer, "on_consume_stop", onConsumeStop);
    }
};

class PyBatchRecordConsumer : public cyc::BatchRecordConsumer {
public:
    using cyc::BatchRecordConsumer::BatchRecordConsumer;

    void consumeBatch(const cyc::RecordReader::RecordBatch& batch) override {
        PYBIND11_OVERRIDE_PURE_NAME(
            void, cyc::BatchRecordConsumer, "consume_batch", consumeBatch, batch);
    }

    void onConsumeStart() override {
        PYBIND11_OVERRIDE_NAME(
            void, cyc::BatchRecordConsumer, "on_consume_start", onConsumeStart);
    }

    void onConsumeStop() override {
        PYBIND11_OVERRIDE_NAME(
            void, cyc::BatchRecordConsumer, "on_consume_stop", onConsumeStop);
    }
};


void bind_consumer(py::module_& m) {
    // ----- RecordConsumer (abstract base) ------------------------------------
    py::class_<cyc::RecordConsumer, PyRecordConsumer>(m, "RecordConsumer",
        "Abstract consumer base class. Subclass in Python and override "
        "consume_record(rec). Use the default constructor + init()/init_zc() "
        "to choose the reader type.")
        .def(py::init<>(),
             "Default constructor — call init() or init_zc() before start().")
        .def(py::init<std::shared_ptr<cyc::RecBuffer>, std::size_t>(),
             py::arg("buffer"), py::arg("reader_batch_size") = 100)
        .def("init",
            [](cyc::RecordConsumer& self,
               std::shared_ptr<cyc::RecBuffer> buf, std::size_t batch) {
                self.init<cyc::RecordReader>(buf, batch);
            },
            py::arg("buffer"), py::arg("reader_batch_size") = 100,
            "Initialise with the default RecordReader (double-buffered).")
        .def("init_zc",
            [](cyc::RecordConsumer& self,
               std::shared_ptr<cyc::RecBuffer> buf, std::size_t batch) {
                self.init<cyc::RecordReaderZC>(buf, batch);
            },
            py::arg("buffer"), py::arg("reader_batch_size") = 100,
            "Initialise with RecordReaderZC (zero-copy, synchronous).")
        .def("start",      &cyc::RecordConsumer::start,
             py::call_guard<py::gil_scoped_release>())
        .def("stop",       &cyc::RecordConsumer::stop,
             py::call_guard<py::gil_scoped_release>(),
             "Stop immediately without draining remaining data.")
        .def("finish",     &cyc::RecordConsumer::finish,
             py::call_guard<py::gil_scoped_release>(),
             "Consume everything up to the current cursor, then stop.")
        .def("is_running", &cyc::RecordConsumer::isRunning);

    // ----- BatchRecordConsumer ----------------------------------------------
    py::class_<cyc::BatchRecordConsumer, cyc::RecordConsumer,
               PyBatchRecordConsumer>(m, "BatchRecordConsumer",
        "Optimised consumer base class for bulk processing. Subclass and "
        "override consume_batch(batch).")
        .def(py::init<>())
        .def(py::init<std::shared_ptr<cyc::RecBuffer>, std::size_t>(),
             py::arg("buffer"), py::arg("reader_batch_size") = 1000);

    // ----- CbfWriter ---------------------------------------------------------
    py::class_<cyc::CbfWriter>(m, "CbfWriter",
        "Asynchronous binary writer that dumps records to a .cbf file.")
        .def(py::init(
            [](const std::string& fn, std::shared_ptr<cyc::RecBuffer> buf,
               bool auto_start, std::size_t batch_size, bool use_zc) {
                return use_zc
                    ? new cyc::CbfWriter(cyc::UseReader<cyc::RecordReaderZC>{},
                                         fn, buf, auto_start, batch_size)
                    : new cyc::CbfWriter(fn, buf, auto_start, batch_size);
            }),
            py::arg("filename"),
            py::arg("buffer"),
            py::arg("auto_start") = true,
            py::arg("batch_size") = 1000,
            py::arg("use_zc")     = false,
            py::call_guard<py::gil_scoped_release>(),
            "use_zc=True picks RecordReaderZC for zero-copy reads from the "
            "source buffer.")
        .def("set_alias", &cyc::CbfWriter::setAlias, py::arg("alias"))

        // Inherited from RecordConsumer — re-declared for convenience.
        .def("start",      &cyc::RecordConsumer::start,
             py::call_guard<py::gil_scoped_release>())
        .def("stop",       &cyc::RecordConsumer::stop,
             py::call_guard<py::gil_scoped_release>())
        .def("finish",     &cyc::RecordConsumer::finish,
             py::call_guard<py::gil_scoped_release>())
        .def("is_running", &cyc::RecordConsumer::isRunning);

    // ----- CsvWriter ---------------------------------------------------------
    py::class_<cyc::CsvWriter>(m, "CsvWriter",
        "Asynchronous CSV writer. Streams records into a .csv file.")
        .def(py::init(
            [](const std::string& fn, std::shared_ptr<cyc::RecBuffer> buf,
               bool auto_start, std::size_t batch_size, bool use_zc) {
                return use_zc
                    ? new cyc::CsvWriter(cyc::UseReader<cyc::RecordReaderZC>{},
                                         fn, buf, auto_start, batch_size)
                    : new cyc::CsvWriter(fn, buf, auto_start, batch_size);
            }),
            py::arg("filename"),
            py::arg("buffer"),
            py::arg("auto_start") = true,
            py::arg("batch_size") = 100,
            py::arg("use_zc")     = false,
            py::call_guard<py::gil_scoped_release>(),
            "use_zc=True picks RecordReaderZC for zero-copy reads.")

        // Inherited from RecordConsumer.
        .def("start",      &cyc::RecordConsumer::start,
             py::call_guard<py::gil_scoped_release>())
        .def("stop",       &cyc::RecordConsumer::stop,
             py::call_guard<py::gil_scoped_release>())
        .def("finish",     &cyc::RecordConsumer::finish,
             py::call_guard<py::gil_scoped_release>())
        .def("is_running", &cyc::RecordConsumer::isRunning);
}

} // namespace cyclibpy
