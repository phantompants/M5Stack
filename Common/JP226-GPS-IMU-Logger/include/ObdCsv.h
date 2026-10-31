#pragma once

#include "ObdProtocol.h"

namespace obd::csv {

// Change the schema tag when these columns change, so an old file can never
// receive rows with a different column layout. Legacy daily files stay intact.
inline constexpr char kFileSuffix[] = "_obd1.csv";
inline constexpr size_t kBufferSize = 1024;
extern const char kColumns[];

// No leading comma or newline. Returns false on overflow; never write a
// partial fragment to SD. Freshness is checked again at the row timestamp.
bool writeFields(const Readings& snapshot, uint32_t nowMs,
                  char* output, size_t capacity);

// KML discovery accepts both legacy daily files and the OBD schema above.
bool isDailyFileName(const char* name, const char* prefix);

}  // namespace obd::csv
