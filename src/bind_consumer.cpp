//
// bind_consumer.cpp — RecordConsumer hierarchy and the concrete file writers
// (CbfWriter, CsvWriter).
//
// v0.4.0: RecordConsumer manages a RecordReaderBase-derived reader chosen via
//         init(UseReader<T>{}, ...) / init_zc().
// v0.5.0: CbfWriter and CsvWriter gained:
//           - default ctor + init() / init_zc() methods
//           - addTimestampSuffix param (default true)
//           - maxRecords param (file rotation, default 0 = disabled)
//           - restart() method (thread-safe file rotation)
//         init() / init_zc() on all consumers now use UseReader<T>{} tag dispatch
//         instead of the old template-parameter form.
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
                self.init(buf, batch);
            },
            py::arg("buffer"), py::arg("reader_batch_size") = 100,
            "Initialise with the default RecordReader (double-buffered).")
        .def("init_zc",
            [](cyc::RecordConsumer& self,
               std::shared_ptr<cyc::RecBuffer> buf, std::size_t batch) {
                self.init(cyc::UseReader<cyc::RecordReaderZC>{}, buf, batch);
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
    // v0.5.0: default ctor + init(), addTimestampSuffix, maxRecords, restart().
    py::class_<cyc::CbfWriter>(m, "CbfWriter",
        "Asynchronous binary writer that dumps records to a .cbf file.\n\n"
        "File rotation: set max_records > 0 to automatically rotate to a new\n"
        "timestamped file when that many records have been written. Call\n"
        "restart() at any time for a manual rotation.")
        .def(py::init<>(),
             "Default constructor — call init() or init_zc() before start().")
        .def(py::init(
            [](const std::string& fn, std::shared_ptr<cyc::RecBuffer> buf,
               bool auto_start, std::size_t batch_size,
               bool add_timestamp_suffix, std::size_t max_records,
               bool use_zc) {
                return use_zc
                    ? new cyc::CbfWriter(cyc::UseReader<cyc::RecordReaderZC>{},
                                         fn, buf, auto_start, batch_size,
                                         add_timestamp_suffix, max_records)
                    : new cyc::CbfWriter(fn, buf, auto_start, batch_size,
                                         add_timestamp_suffix, max_records);
            }),
            py::arg("filename"),
            py::arg("buffer"),
            py::arg("auto_start")            = true,
            py::arg("batch_size")            = 1000,
            py::arg("add_timestamp_suffix")  = true,
            py::arg("max_records")           = std::size_t(0),
            py::arg("use_zc")                = false,
            py::call_guard<py::gil_scoped_release>(),
            "use_zc=True picks RecordReaderZC for zero-copy reads from the "
            "source buffer.\n"
            "add_timestamp_suffix inserts the current time before the extension.\n"
            "max_records>0 rotates to a new file automatically.")
        .def("init",
            [](cyc::CbfWriter& w,
               const std::string& fn, std::shared_ptr<cyc::RecBuffer> buf,
               bool auto_start, std::size_t batch_size,
               bool add_timestamp_suffix, std::size_t max_records) {
                w.init(fn, buf, auto_start, batch_size,
                       add_timestamp_suffix, max_records);
            },
            py::arg("filename"),
            py::arg("buffer"),
            py::arg("auto_start")            = true,
            py::arg("batch_size")            = 1000,
            py::arg("add_timestamp_suffix")  = true,
            py::arg("max_records")           = std::size_t(0),
            py::call_guard<py::gil_scoped_release>(),
            "Initialise a default-constructed CbfWriter with the default "
            "RecordReader (double-buffered).")
        .def("init_zc",
            [](cyc::CbfWriter& w,
               const std::string& fn, std::shared_ptr<cyc::RecBuffer> buf,
               bool auto_start, std::size_t batch_size,
               bool add_timestamp_suffix, std::size_t max_records) {
                w.init(cyc::UseReader<cyc::RecordReaderZC>{},
                       fn, buf, auto_start, batch_size,
                       add_timestamp_suffix, max_records);
            },
            py::arg("filename"),
            py::arg("buffer"),
            py::arg("auto_start")            = true,
            py::arg("batch_size")            = 1000,
            py::arg("add_timestamp_suffix")  = true,
            py::arg("max_records")           = std::size_t(0),
            py::call_guard<py::gil_scoped_release>(),
            "Initialise a default-constructed CbfWriter with RecordReaderZC "
            "(zero-copy, synchronous).")
        .def("set_alias", &cyc::CbfWriter::setAlias, py::arg("alias"))
        .def("restart",   &cyc::CbfWriter::restart,
             "Close the current file and open a new one (with a fresh "
             "timestamp suffix if enabled). Thread-safe; data written before "
             "this call lands in the old file.")

        // Inherited from RecordConsumer — re-declared for convenience.
        .def("start",      &cyc::RecordConsumer::start,
             py::call_guard<py::gil_scoped_release>())
        .def("stop",       &cyc::RecordConsumer::stop,
             py::call_guard<py::gil_scoped_release>())
        .def("finish",     &cyc::RecordConsumer::finish,
             py::call_guard<py::gil_scoped_release>())
        .def("is_running", &cyc::RecordConsumer::isRunning);

    // ----- CsvWriter ---------------------------------------------------------
    // v0.5.0: same additions as CbfWriter; default init() uses RecordReaderZC.
    py::class_<cyc::CsvWriter>(m, "CsvWriter",
        "Asynchronous CSV writer. Streams records into a .csv file.\n\n"
        "File rotation: set max_records > 0 to automatically rotate to a new\n"
        "timestamped file when that many records have been written. Call\n"
        "restart() at any time for a manual rotation.")
        .def(py::init<>(),
             "Default constructor — call init() or init_zc() before start().")
        .def(py::init(
            [](const std::string& fn, std::shared_ptr<cyc::RecBuffer> buf,
               bool auto_start, std::size_t batch_size,
               bool add_timestamp_suffix, std::size_t max_records,
               bool use_zc) {
                return use_zc
                    ? new cyc::CsvWriter(cyc::UseReader<cyc::RecordReaderZC>{},
                                         fn, buf, auto_start, batch_size,
                                         add_timestamp_suffix, max_records)
                    : new cyc::CsvWriter(fn, buf, auto_start, batch_size,
                                         add_timestamp_suffix, max_records);
            }),
            py::arg("filename"),
            py::arg("buffer"),
            py::arg("auto_start")            = true,
            py::arg("batch_size")            = 100,
            py::arg("add_timestamp_suffix")  = true,
            py::arg("max_records")           = std::size_t(0),
            py::arg("use_zc")                = false,
            py::call_guard<py::gil_scoped_release>(),
            "use_zc=True picks RecordReaderZC for zero-copy reads.\n"
            "add_timestamp_suffix inserts the current time before the extension.\n"
            "max_records>0 rotates to a new file automatically.")
        .def("init",
            [](cyc::CsvWriter& w,
               const std::string& fn, std::shared_ptr<cyc::RecBuffer> buf,
               bool auto_start, std::size_t batch_size,
               bool add_timestamp_suffix, std::size_t max_records) {
                w.init(fn, buf, auto_start, batch_size,
                       add_timestamp_suffix, max_records);
            },
            py::arg("filename"),
            py::arg("buffer"),
            py::arg("auto_start")            = true,
            py::arg("batch_size")            = 100,
            py::arg("add_timestamp_suffix")  = true,
            py::arg("max_records")           = std::size_t(0),
            py::call_guard<py::gil_scoped_release>(),
            "Initialise a default-constructed CsvWriter with the default "
            "RecordReaderZC (zero-copy, synchronous).")
        .def("init_zc",
            [](cyc::CsvWriter& w,
               const std::string& fn, std::shared_ptr<cyc::RecBuffer> buf,
               bool auto_start, std::size_t batch_size,
               bool add_timestamp_suffix, std::size_t max_records) {
                w.init(cyc::UseReader<cyc::RecordReaderZC>{},
                       fn, buf, auto_start, batch_size,
                       add_timestamp_suffix, max_records);
            },
            py::arg("filename"),
            py::arg("buffer"),
            py::arg("auto_start")            = true,
            py::arg("batch_size")            = 100,
            py::arg("add_timestamp_suffix")  = true,
            py::arg("max_records")           = std::size_t(0),
            py::call_guard<py::gil_scoped_release>(),
            "Initialise a default-constructed CsvWriter with RecordReaderZC "
            "(zero-copy, synchronous).")
        .def("restart",    &cyc::CsvWriter::restart,
             "Close the current file and open a new one (with a fresh "
             "timestamp suffix if enabled). Thread-safe.")

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
