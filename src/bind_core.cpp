//
// bind_core.cpp — Common (DataType), PAttr, PReg, RecRule, BitRef, Record.
// All signatures verified against Core/*.h (develop @ be6d78a).
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>

#include <cstring>
#include <string>

#include "Core/Common.h"
#include "Core/PAttr.h"
#include "Core/PReg.h"
#include "Core/RecRule.h"
#include "Core/Record.h"

namespace py = pybind11;

namespace cyclibpy {

void bind_core(py::module_& m) {
    // ----- DataType ----------------------------------------------------------
    // Full set from Common.h. The pointer type dtPtr is exposed for
    // completeness, but it has no numpy mapping.
    py::enum_<cyc::DataType>(m, "DataType", "Field types used in CycLib records")
        .value("Undefine", cyc::DataType::dtUndefine)
        .value("Bool",     cyc::DataType::dtBool)
        .value("Char",     cyc::DataType::dtChar)
        .value("Void",     cyc::DataType::dtVoid)
        .value("Int8",     cyc::DataType::dtInt8)
        .value("UInt8",    cyc::DataType::dtUInt8)
        .value("Int16",    cyc::DataType::dtInt16)
        .value("UInt16",   cyc::DataType::dtUInt16)
        .value("Int32",    cyc::DataType::dtInt32)
        .value("UInt32",   cyc::DataType::dtUInt32)
        .value("Int64",    cyc::DataType::dtInt64)
        .value("UInt64",   cyc::DataType::dtUInt64)
        .value("Float",    cyc::DataType::dtFloat)
        .value("Double",   cyc::DataType::dtDouble)
        .value("Ptr",      cyc::DataType::dtPtr)
        .export_values();

    m.def("get_type_size",         &cyc::getTypeSize, py::arg("type"));
    m.def("data_type_to_string",   &cyc::dataTypeToString, py::arg("type"));
    m.def("data_type_from_string", &cyc::dataTypeFromString, py::arg("str"));
    m.def("get_current_epoch_time", &cyc::get_current_epoch_time);

    // ----- PAttr -------------------------------------------------------------
    // name is char[26]; we expose it through a property that converts to/from
    // std::string transparently.
    py::class_<cyc::PAttr>(m, "PAttr", "Describes one field in a record schema")
        .def(py::init<>())
        .def(py::init(
            [](const std::string& name, cyc::DataType type, std::size_t count) {
                return cyc::PAttr(name.c_str(), type, count);
            }),
            py::arg("name"), py::arg("type"), py::arg("count") = 1,
            "Plain typed field.")
        .def(py::init(
            [](const std::string& name, cyc::DataType type,
               const std::vector<std::string>& bit_defs) {
                return cyc::PAttr(name.c_str(), type, bit_defs);
            }),
            py::arg("name"), py::arg("type"), py::arg("bit_defs"),
            "Integer field with named bit flags.\n"
            "bit_defs: list ordered from LSB upwards; a numeric string means "
            "'skip N bits', a regular string registers a named bit in PReg.")
        .def_readwrite("id",     &cyc::PAttr::id)
        .def_property("name",
            [](const cyc::PAttr& a) { return std::string(a.name); },
            [](cyc::PAttr& a, const std::string& n) {
                std::strncpy(a.name, n.c_str(), sizeof(a.name) - 1);
                a.name[sizeof(a.name) - 1] = '\0';
            })
        .def_readwrite("type",   &cyc::PAttr::type)
        .def_readwrite("count",  &cyc::PAttr::count)
        .def_readwrite("offset", &cyc::PAttr::offset)
        .def_readwrite("bit_ids", &cyc::PAttr::bitIds)
        .def("has_bit_fields", &cyc::PAttr::hasBitFields)
        .def("get_size",       &cyc::PAttr::getSize)
        .def("__repr__", [](const cyc::PAttr& a) {
            return "<PAttr id=" + std::to_string(a.id) +
                   " name='" + std::string(a.name) + "'" +
                   " type=" + cyc::dataTypeToString(a.type) +
                   " count=" + std::to_string(a.count) +
                   " offset=" + std::to_string(a.offset) +
                   (a.bitIds.empty() ? "" : " bits=" + std::to_string(a.bitIds.size())) +
                   ">";
        });

    // ----- PReg (singleton with static methods) ------------------------------
    py::class_<cyc::PReg>(m, "PReg",
        "Global registry that maps names to integer IDs for O(1) lookup")
        .def_static("get_id",   &cyc::PReg::getID,   py::arg("name"))
        .def_static("get_name", &cyc::PReg::getName, py::arg("id"));

    // ----- BitRef ------------------------------------------------------------
    py::class_<cyc::BitRef>(m, "BitRef", "Reference to a named bit within a field")
        .def_readonly("field_id", &cyc::BitRef::fieldId)
        .def_readonly("bit_pos",  &cyc::BitRef::bitPos)
        .def("__repr__", [](const cyc::BitRef& b) {
            return "<BitRef field_id=" + std::to_string(b.fieldId) +
                   " bit_pos=" + std::to_string(b.bitPos) + ">";
        });

    // ----- RecRule -----------------------------------------------------------
    py::class_<cyc::RecRule>(m, "RecRule", "CycLib record schema")
        .def(py::init<>())
        .def(py::init<const std::vector<cyc::PAttr>&, bool>(),
             py::arg("attrs"), py::arg("align") = false,
             "Construct a rule. When align=True fields are sorted by "
             "decreasing element size and trailing padding is added so "
             "every field sits on a naturally-aligned boundary.")
        .def("init",              &cyc::RecRule::init,
             py::arg("attrs"), py::arg("align") = false,
             "Initialise the rule. See constructor for `align` semantics.")
        .def("build_header",      &cyc::RecRule::buildHeader)
        .def("get_rec_size",      &cyc::RecRule::getRecSize)
        .def("get_offset_by_index", &cyc::RecRule::getOffsetByIndex, py::arg("index"))
        .def("get_offset_by_id",  &cyc::RecRule::getOffsetById, py::arg("id"))
        .def("get_type",          &cyc::RecRule::getType,       py::arg("id"))
        .def("get_bit_ref",       &cyc::RecRule::getBitRef,     py::arg("id"))
        .def("get_attributes",    &cyc::RecRule::getAttributes,
             py::return_value_policy::reference_internal)
        .def("to_text",           &cyc::RecRule::toText)
        .def_static("from_text",  &cyc::RecRule::fromText,      py::arg("text"))
        // Convenience dunders
        .def("__len__",   [](const cyc::RecRule& r) { return r.getAttributes().size(); })
        .def("__contains__", [](const cyc::RecRule& r, const std::string& n) {
            for (const auto& a : r.getAttributes())
                if (n == a.name) return true;
            return false;
        })
        .def("__iter__",  [](const cyc::RecRule& r) {
            return py::make_iterator(r.getAttributes().begin(),
                                     r.getAttributes().end());
        }, py::keep_alive<0, 1>());

    // ----- Record ------------------------------------------------------------
    // Record holds a `const RecRule&`, so its lifetime is bounded by the
    // lifetime of the source (RecordReader / RecBuffer). We attach keep_alive
    // wherever a Record leaks into Python to prevent dangling references.
    //
    // The const/non-const overloads generated by DECLARE_RECORD_ACCESSORS are
    // wrapped in lambdas to avoid verbose static_cast<>s.
    auto rec = py::class_<cyc::Record>(m, "Record",
        "Read/write view of a single record over raw memory.");

    rec.def("is_valid", &cyc::Record::isValid)
       .def("clear",    &cyc::Record::clear)
       .def("get_size", &cyc::Record::getSize)
       .def("set_data",
            [](cyc::Record& r, py::buffer buf) {
                py::buffer_info info = buf.request(true);
                r.setData(info.ptr);
            },
            py::arg("buffer"),
            "Point the record at a new memory block (e.g. a numpy array). "
            "The buffer must remain alive while the record is in use.")
       .def("data_ptr",
            [](const cyc::Record& r) -> uintptr_t {
                return reinterpret_cast<uintptr_t>(r.data());
            },
            "Return the raw data pointer as an integer (for advanced use).");

    // Generic double-based getter/setter — the primary API for generic
    // Python code that doesn't care about the underlying type.
    rec.def("get_value",
            [](const cyc::Record& r, int id, std::size_t index) {
                return r.getValue(id, index);
            },
            py::arg("id"), py::arg("index") = 0)
       .def("set_value",
            [](cyc::Record& r, int id, double val, std::size_t index) {
                r.setValue(id, val, index);
            },
            py::arg("id"), py::arg("value"), py::arg("index") = 0);

    // Named bit access.
    rec.def("get_bit",
            [](const cyc::Record& r, int id) { return r.getBit(id); },
            py::arg("id"))
       .def("set_bit",
            [](cyc::Record& r, int id, bool v) { r.setBit(id, v); },
            py::arg("id"), py::arg("value"))
       .def("get_bit_value",
            [](const cyc::Record& r, int id) { return r.getBitValue(id); },
            py::arg("id"))
       .def("set_bit_value",
            [](cyc::Record& r, int id, double v) { r.setBitValue(id, v); },
            py::arg("id"), py::arg("value"));

    // Typed getters and setters.
#define BIND_ACCESSOR(NAME, CPP_TYPE, PY_NAME)                                 \
    rec.def("get_" PY_NAME,                                                    \
            [](const cyc::Record& r, int id, std::size_t i) {                  \
                return r.get##NAME(id, i);                                     \
            },                                                                 \
            py::arg("id"), py::arg("index") = 0);                              \
    rec.def("set_" PY_NAME,                                                    \
            [](cyc::Record& r, int id, CPP_TYPE v, std::size_t i) {            \
                r.set##NAME(id, v, i);                                         \
            },                                                                 \
            py::arg("id"), py::arg("value"), py::arg("index") = 0)

    BIND_ACCESSOR(Bool,   bool,     "bool");
    BIND_ACCESSOR(Char,   char,     "char");
    BIND_ACCESSOR(Int8,   int8_t,   "int8");
    BIND_ACCESSOR(UInt8,  uint8_t,  "uint8");
    BIND_ACCESSOR(Int16,  int16_t,  "int16");
    BIND_ACCESSOR(UInt16, uint16_t, "uint16");
    BIND_ACCESSOR(Int32,  int32_t,  "int32");
    BIND_ACCESSOR(UInt32, uint32_t, "uint32");
    BIND_ACCESSOR(Int64,  int64_t,  "int64");
    BIND_ACCESSOR(UInt64, uint64_t, "uint64");
    BIND_ACCESSOR(Float,  float,    "float");
    BIND_ACCESSOR(Double, double,   "double");
#undef BIND_ACCESSOR
}

} // namespace cyclibpy
