// Force-included into the FidelityFX SDK targets on MinGW (gpu/CMakeLists.txt). The SDK relies on
// MSVC's headers pulling these in transitively; newer libc++ does not. The list mirrors what
// fsr-vulkan's ffx_1_1_4_portability.hpp includes on other hosts, plus <new> for placement new.
#pragma once
#include <stdlib.h>  // _countof
#include <wchar.h>   // swprintf_s
#ifdef __cplusplus
#include <cerrno>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <new>
#endif
