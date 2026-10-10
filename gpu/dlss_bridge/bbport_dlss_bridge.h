// SPDX-FileCopyrightText: Copyright 2026 IFreemz (shadps4_dlss bridge), bbport contributors
// SPDX-License-Identifier: MIT
//
// C interface between bbport and bbport_dlss.dll. The port contains no NVIDIA code: it loads the
// bridge at run time when it is present (next to bb-probe.exe, with nvngx_dlss.dll) and keeps
// its other upscalers when it is absent. Adapted from IFreemz/shadPS4-Bloodborne-DLSS-FSR.

#pragma once

#include <stdint.h>
#include <vulkan/vulkan.h>

#ifdef __cplusplus
extern "C" {
#endif

// 2 added DLSS Frame Generation (the last four BbDlssApi members); an abi 1 bridge has the members
// before them and nothing else, and the port then runs without frame generation.
#define BBPORT_DLSS_BRIDGE_ABI 2

typedef void (*BbDlssLogFn)(int warning, const char* message);

typedef struct BbDlssImage {
    VkImage image;
    VkImageView view;
    VkImageSubresourceRange range;
    VkFormat format;
    uint32_t width;
    uint32_t height;
} BbDlssImage;

typedef struct BbDlssFeature {
    uint32_t input_width;
    uint32_t input_height;
    uint32_t output_width;
    uint32_t output_height;
    int32_t quality; // 0 DLAA, 1 Quality, 2 Balanced, 3 Performance, 4 Ultra Performance
    int32_t depth_inverted;
    uint32_t preset; // NVSDK_NGX_DLSS_Hint_Render_Preset value, 0 = driver default
    int32_t hdr;     // 1: linear HDR colour with automatic exposure, 0: tonemapped colour
} BbDlssFeature;

typedef struct BbDlssEvaluate {
    BbDlssImage color;  // sampled
    BbDlssImage depth;  // sampled, hardware depth
    BbDlssImage motion; // sampled, render-resolution pixels, current to previous
    BbDlssImage output; // storage, general layout
    float jitter_x;
    float jitter_y;
    int32_t reset;
    float frame_ms;
    float sharpness; // 0..1; 0 leaves the output unsharpened
} BbDlssEvaluate;

// DLSS Frame Generation (abi 2): one interpolated frame between the previous and the current game
// frame. All matrices are row-major for row vectors (v * M), D3D clip space (+y up), and carry no
// jitter; the images are in General layout.
typedef struct BbDlssFrameGenFeature {
    uint32_t width;  // the backbuffer (the finished frame, UI included) and the output
    uint32_t height;
    VkFormat format; // the backbuffer format
    uint32_t render_width;  // the size of the depth and motion vectors
    uint32_t render_height;
} BbDlssFrameGenFeature;

typedef struct BbDlssFrameGenEvaluate {
    BbDlssImage backbuffer; // sampled: this frame as it is shown, with the UI
    BbDlssImage depth;      // sampled, hardware depth (0 near, 1 far unless depth_inverted)
    BbDlssImage motion;     // sampled, render-resolution pixels, current to previous, camera included
    BbDlssImage hudless;    // sampled, the same frame without the UI; image == VK_NULL_HANDLE omits it
    BbDlssImage output;     // storage, the interpolated frame: backbuffer's size and format
    float view_to_clip[16];
    float clip_to_view[16];
    float clip_to_prev_clip[16];
    float prev_clip_to_clip[16];
    float jitter_x; // clip space
    float jitter_y;
    float camera_pos[3]; // world space
    float camera_up[3];
    float camera_right[3];
    float camera_forward[3];
    float camera_near;
    float camera_far;
    float camera_fov; // vertical, radians
    float camera_aspect;
    int32_t reset; // the previous frame is unrelated to this one
    int32_t depth_inverted;
} BbDlssFrameGenEvaluate;

typedef struct BbDlssApi {
    uint32_t abi;
    // UTF-16 paths: the directory with nvngx_dlss.dll, and a writable data directory.
    int32_t (*Configure)(const wchar_t* dll_directory, const wchar_t* data_directory,
                         BbDlssLogFn log);
    // Vulkan extensions NGX needs: Super Resolution's, and from abi 2 Frame Generation's too
    // (deduplicated; the device ones only when Frame Generation is supported on this GPU). The
    // arrays stay valid until Shutdown.
    int32_t (*InstanceExtensions)(uint32_t* count, const VkExtensionProperties** extensions);
    int32_t (*DeviceExtensions)(VkInstance instance, VkPhysicalDevice physical, uint32_t* count,
                                const VkExtensionProperties** extensions);
    int32_t (*Initialize)(VkInstance instance, VkPhysicalDevice physical, VkDevice device,
                          PFN_vkGetInstanceProcAddr get_instance_proc,
                          PFN_vkGetDeviceProcAddr get_device_proc);
    int32_t (*CreateFeature)(VkCommandBuffer command, const BbDlssFeature* feature);
    int32_t (*Evaluate)(VkCommandBuffer command, const BbDlssEvaluate* evaluate);
    // The caller must have waited for the GPU work that used the feature.
    void (*ReleaseFeature)(void);
    void (*Shutdown)(void);
    // ---- abi 2: DLSS Frame Generation (needs nvngx_dlssg.dll next to nvngx_dlss.dll) ------------
    // After Initialize: whether NGX can run it here (GeForce RTX 40 or newer, a current driver,
    // Windows hardware-accelerated GPU scheduling). `init_result` is NGX's own result code.
    int32_t (*FrameGenAvailable)(int32_t* available, int32_t* init_result);
    // Needs an open command buffer. Replaces a previous feature.
    int32_t (*CreateFrameGen)(VkCommandBuffer command, const BbDlssFrameGenFeature* feature);
    int32_t (*EvaluateFrameGen)(VkCommandBuffer command, const BbDlssFrameGenEvaluate* evaluate);
    // The caller must have waited for the GPU work that used the feature.
    void (*ReleaseFrameGen)(void);
} BbDlssApi;

typedef const BbDlssApi* (*BbDlssGetApiFn)(void);

#ifdef __cplusplus
}
#endif
