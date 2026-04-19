//
// bind_cbf.cpp — CbfReader + low-level CbfFile, CbfMode, CbfSectionType,
// CbfSectionHeader.
//
// High-level flow: use read_cbf_to_array(path) or cyclib.CbfReader.
// Low-level flow: use CbfFile directly for section-by-section inspection.
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>

#include <cstring>
#include <chrono>
#include <thread>

#include "Cbf/CbfReader.h"
#include "Cbf/CbfFile.h"
#include "Cbf/CbfDefs.h"
#include "RecordProducer.h"
#include "RecordReader.h"

#include "numpy_bridge.h"

namespace py = pybind11;

namespace cyclibpy {

void bind_cbf(py::module_& m) {
    // ----- CbfSectionType ----------------------------------------------------
    py::enum_<cyc::CbfSectionType>(m, "CbfSectionType")
        .value("Header", cyc::CbfSectionType::Header)
        .value("Data",   cyc::CbfSectionType::Data)
        .export_values();

    m.attr("CBF_SECTION_MARKER") = py::int_(cyc::CBF_SECTION_MARKER);

    // ----- CbfSectionHeader (read-only view) --------------------------------
    py::class_<cyc::CbfSectionHeader>(m, "CbfSectionHeader",
        "Metadata prefixing every CBF section (24 bytes, packed).")
        .def(py::init<>())
        .def_readwrite("marker",      &cyc::CbfSectionHeader::marker)
        .def_readwrite("type",        &cyc::CbfSectionHeader::type)
        .def_readwrite("body_length", &cyc::CbfSectionHeader::bodyLength)
        .def_property("name",
            [](const cyc::CbfSectionHeader& h) { return std::string(h.name); },
            [](cyc::CbfSectionHeader& h, const std::string& n) {
                std::strncpy(h.name, n.c_str(), sizeof(h.name) - 1);
                h.name[sizeof(h.name) - 1] = '\0';
            })
        .def("__repr__", [](const cyc::CbfSectionHeader& h) {
            return "<CbfSectionHeader name='" + std::string(h.name) +
                   "' type=" + std::to_string(h.type) +
                   " body_length=" + std::to_string(h.bodyLength) + ">";
        });

    // ----- CbfMode -----------------------------------------------------------
    py::enum_<cyc::CbfMode>(m, "CbfMode")
        .value("Read",  cyc::CbfMode::Read)
        .value("Write", cyc::CbfMode::Write)
        .export_values();

    // ----- CbfFile (low-level API) -------------------------------------------
    py::class_<cyc::CbfFile>(m, "CbfFile",
        "Low-level wrapper for CBF files. Use the high-level CbfReader / "
        "CbfWriter unless you need direct section access.")
        .def(py::init<>())
        .def("open",  &cyc::CbfFile::open, py::arg("filename"), py::arg("mode"),
             py::call_guard<py::gil_scoped_release>())
        .def("close", &cyc::CbfFile::close)
        .def("is_open", &cyc::CbfFile::isOpen)
        .def("is_good", &cyc::CbfFile::isGood)

        // Write API
        .def("set_alias",           &cyc::CbfFile::setAlias, py::arg("alias"))
        .def("write_header",        &cyc::CbfFile::writeHeader, py::arg("rule"))
        .def("begin_data_section",  &cyc::CbfFile::beginDataSection)
        .def("write_record",        &cyc::CbfFile::writeRecord, py::arg("record"))
        .def("end_data_section",    &cyc::CbfFile::endDataSection)

        // Read API
        .def("read_section_header",
            [](cyc::CbfFile& f) -> py::object {
                cyc::CbfSectionHeader h;
                if (!f.readSectionHeader(h)) return py::none();
                return py::cast(h);
            },
            "Read the next section header or return None on EOF.")
        .def("read_rule",
            [](cyc::CbfFile& f, const cyc::CbfSectionHeader& h) -> py::object {
                cyc::RecRule rule;
                if (!f.readRule(h, rule)) return py::none();
                return py::cast(std::move(rule));
            },
            py::arg("section_header"))
        .def("read_record", &cyc::CbfFile::readRecord, py::arg("record"))
        .def("skip_section", &cyc::CbfFile::skipSection, py::arg("section_header"))

        // Context-manager
        .def("__enter__", [](py::object self) { return self; })
        .def("__exit__",  [](cyc::CbfFile& f, py::object, py::object, py::object) {
            f.close();
            return false;
        });

    // ----- CbfReader (high-level, BatchRecordProducer) ----------------------
    py::class_<cyc::CbfReader>(m, "CbfReader",
        "Offline reader for .cbf files. Inherits from BatchRecordProducer: "
        "data is read into a background RecBuffer and accessed via "
        "get_buffer() plus a RecordReader.")

        .def(py::init<const std::string&, std::size_t, bool, std::size_t>(),
             py::arg("filename"),
             py::arg("buffer_capacity")   = 100000,
             py::arg("auto_start")        = true,
             py::arg("writer_batch_size") = 1000,
             py::call_guard<py::gil_scoped_release>())

        .def("is_valid", &cyc::CbfReader::isValid)
        .def("start",      &cyc::RecordProducer::start,
             py::call_guard<py::gil_scoped_release>())
        .def("stop",       &cyc::RecordProducer::stop,
             py::call_guard<py::gil_scoped_release>())
        .def("join",       &cyc::RecordProducer::join,
             py::call_guard<py::gil_scoped_release>())
        .def("is_running", &cyc::RecordProducer::isRunning)
        .def("get_buffer", &cyc::RecordProducer::getBuffer);

    // ----- Convenience helper: read whole .cbf into numpy --------------------
    m.def("read_cbf_to_array",
        [](const std::string& path, std::size_t batch_capacity) -> py::object {
            cyc::CbfReader cbf(path, batch_capacity * 4, true, batch_capacity);
            if (!cbf.isValid()) {
                throw std::runtime_error("CbfReader: cannot open '" + path + "'");
            }

            auto buf = cbf.getBuffer();
            cyc::RecordReader reader(buf, batch_capacity);

            const cyc::RecRule& rule = reader.getRule();
            const std::size_t recSize = rule.getRecSize();

            py::module_ np = py::module_::import("numpy");
            py::object dtype = dtype_from_rule(rule);
            py::list parts;

            {
                py::gil_scoped_release release;
                using namespace std::chrono_literals;
                while (true) {
                    auto batch = reader.nextBatch(batch_capacity, false);
                    if (batch.isValid() && batch.count > 0) {
                        py::gil_scoped_acquire gil;
                        py::array arr = np.attr("empty")(batch.count, dtype);
                        std::memcpy(arr.mutable_data(), batch.data,
                                    batch.count * recSize);
                        parts.append(std::move(arr));
                    } else if (!cbf.isRunning() && buf->size() == 0) {
                        break;
                    } else {
                        std::this_thread::sleep_for(500us);
                    }
                }
            }

            if (parts.empty()) return np.attr("empty")(0, dtype);
            return np.attr("concatenate")(parts);
        },
        py::arg("path"), py::arg("batch_capacity") = 10000,
        "Read an entire .cbf file into a single numpy structured array.");
}

} // namespace cyclibpy
