#pragma once
//
// numpy_bridge.h — translation of cyc::DataType to numpy and construction of
// a structured dtype from a RecRule. All method names are verified against
// the CycLib headers.
//

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <cstddef>

#include "Core/Common.h"
#include "Core/RecRule.h"
#include "Core/PAttr.h"

namespace cyclibpy {

namespace py = pybind11;

// NumPy format string for a DataType. Throws for dtUndefine / dtVoid / dtPtr.
const char* numpy_format_for(cyc::DataType t);

// True if the type can be represented in a numpy structured dtype
// (everything from dtBool through dtDouble).
bool is_dtype_mappable(cyc::DataType t);

// Builds a numpy.dtype from a RecRule using getAttributes() and PAttr::offset.
// Bit-field attributes (PAttr::hasBitFields()) are represented as their
// containing integer word; individual bits are accessed via Record::getBit.
py::object dtype_from_rule(const cyc::RecRule& rule);

// Creates a zero-copy numpy view over an externally-owned contiguous block of
// records. `owner` is any py::object that must stay alive as long as the
// returned ndarray does.
py::array view_structured(const cyc::RecRule& rule,
                          const void* data,
                          std::size_t record_count,
                          py::object owner);

} // namespace cyclibpy
