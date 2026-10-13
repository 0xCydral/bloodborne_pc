// Force-included into every C and C++ file of the GPU build on MinGW (gpu/CMakeLists.txt). The
// third-party code (FidelityFX SDK, sirit, ...) uses _countof, swprintf_s, log2, std::abort and placement
// new without including their headers, relying on other headers pulling them in; newer libc++ does not.
#pragma once
#include <stdlib.h>  // _countof
#include <wchar.h>   // swprintf_s
#ifdef __cplusplus
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <new>
#endif
