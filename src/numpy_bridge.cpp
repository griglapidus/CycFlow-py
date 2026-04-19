#include "numpy_bridge.h"

#include <stdexcept>
#include <string>
#include <cstring>

namespace cyclibpy {

const char* numpy_format_for(cyc::DataType t) {
    using DT = cyc::DataType;
    switch (t) {
        case DT::dtBool:   return "?";
        case DT::dtChar:   return "S1";   // single byte as a bytes scalar
        case DT::dtInt8:   return "<i1";
        case DT::dtUInt8:  return "<u1";
        case DT::dtInt16:  return "<i2";
        case DT::dtUInt16: return "<u2";
        case DT::dtInt32:  return "<i4";
        case DT::dtUInt32: return "<u4";
        case DT::dtInt64:  return "<i8";
        case DT::dtUInt64: return "<u8";
        case DT::dtFloat:  return "<f4";
        case DT::dtDouble: return "<f8";
        default:
            throw std::runtime_error(
                std::string("numpy_format_for: unsupported DataType (") +
                cyc::dataTypeToString(t) + ")");
    }
}

bool is_dtype_mappable(cyc::DataType t) {
    using DT = cyc::DataType;
    switch (t) {
        case DT::dtBool: case DT::dtChar:
        case DT::dtInt8: case DT::dtUInt8:
        case DT::dtInt16: case DT::dtUInt16:
        case DT::dtInt32: case DT::dtUInt32:
        case DT::dtInt64: case DT::dtUInt64:
        case DT::dtFloat: case DT::dtDouble:
            return true;
        default:
            return false; // dtUndefine, dtVoid, dtPtr
    }
}

py::object dtype_from_rule(const cyc::RecRule& rule) {
    py::module_ np = py::module_::import("numpy");

    py::list names, formats, offsets;
    const auto& attrs = rule.getAttributes();

    for (const cyc::PAttr& attr : attrs) {
        if (!is_dtype_mappable(attr.type)) {
            // Skip dtPtr / dtVoid / dtUndefine — they cannot be represented
            // meaningfully in a numpy structured dtype.
            continue;
        }

        names.append(py::str(attr.name));  // char[26] -> Python str

        if (attr.count > 1) {
            // Subarray dtype: ("<f4", (count,))
            formats.append(
                py::make_tuple(py::str(numpy_format_for(attr.type)),
                               py::make_tuple(attr.count)));
        } else {
            formats.append(py::str(numpy_format_for(attr.type)));
        }

        offsets.append(attr.offset);
    }

    py::dict spec;
    spec["names"]    = names;
    spec["formats"]  = formats;
    spec["offsets"]  = offsets;
    spec["itemsize"] = rule.getRecSize();

    return np.attr("dtype")(spec);
}

py::array view_structured(const cyc::RecRule& rule,
                          const void* data,
                          std::size_t record_count,
                          py::object owner)
{
    py::object dtype = dtype_from_rule(rule);
    return py::array(
        dtype,
        { record_count },
        { rule.getRecSize() },   // strides
        data,
        std::move(owner));        // base — keeps the memory source alive
}

} // namespace cyclibpy
