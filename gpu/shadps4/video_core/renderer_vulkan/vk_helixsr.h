// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstdint>
#include <memory>
#include <string>

// C Vulkan types only: vulkan.hpp translation units include this header too.
#include <vulkan/vulkan_core.h>

namespace Vulkan::HelixSr {

/// What the bridge needs from the renderer's Vulkan device. Every Vulkan function the bridge calls
/// is loaded through the two procedure getters, so it never links against the Vulkan loader.
struct DeviceInfo {
    VkInstance instance{};
    VkPhysicalDevice physical{};
    VkDevice device{};
    uint32_t queue_family{};
    PFN_vkGetInstanceProcAddr get_instance_proc{};
    PFN_vkGetDeviceProcAddr get_device_proc{};
    uint8_t luid[8]{}; ///< VkPhysicalDeviceIDProperties::deviceLUID of `physical`
};

struct FrameParams {
    float jitter_x{}, jitter_y{}; ///< in render pixels
    float motion_scale_x{1.0f}, motion_scale_y{1.0f};
    uint32_t render_w{}, render_h{};
    float frame_ms{16.6f};
    float pre_exposure{1.0f};
    float near_plane{0.1f}, far_plane{1000.0f}, fov_y{1.0f};
    bool reset{};
    bool sharpen{};
    float sharpness{};
};

/// Runs a Direct3D 12 FidelityFX-API upscaler DLL (FSR 3.1 API: HelixSR, AMD's own FSR DLL) on the
/// Vulkan device's GPU. Images and one timeline fence are shared between the two APIs; a frame is
///   RecordInputs() -> submit with SIGNAL Semaphore()=v -> Dispatch(v) = w -> RecordOutput() -> submit
///   with WAIT Semaphore()=w.
/// Frames must be submitted in that order: the fence value only ever rises. Windows only; elsewhere
/// every call fails with an error. No exceptions: errors come back as strings.
class Bridge {
public:
    Bridge();
    ~Bridge();
    Bridge(const Bridge&) = delete;
    Bridge& operator=(const Bridge&) = delete;

    /// Checks the device's external memory and semaphore support, loads the DLL (absolute path)
    /// and creates the D3D12 device on the Vulkan device's adapter.
    bool Init(const DeviceInfo& info, const wchar_t* dll_path, std::string* error);

    /// (Re)creates the FFX context and the shared images. The caller guarantees the GPU is idle.
    /// Supported formats, anything else fails with a message before the current setup is touched:
    /// colour R16G16B16A16_SFLOAT, R8G8B8A8_UNORM/SRGB, B10G11R11_UFLOAT_PACK32 or
    /// A2B10G10R10_UNORM_PACK32; depth D32_SFLOAT or D32_SFLOAT_S8_UINT (the depth aspect is copied);
    /// motion vectors R16G16_SFLOAT. The images given to RecordInputs must use these formats.
    /// Fails, leaving the old setup in place, when D3D12 may still be using it (bounded wait) or the
    /// bridge is broken.
    bool Configure(uint32_t max_render_w, uint32_t max_render_h, uint32_t out_w, uint32_t out_h,
                   VkFormat color_format, VkFormat depth_format, VkFormat motion_format,
                   uint32_t ffx_create_flags, std::string* error);

    /// Copies the game's images (all VK_IMAGE_LAYOUT_GENERAL, in the formats given to Configure) into
    /// the shared inputs and releases them to D3D12. The first call after Configure also gives the
    /// shared output its layout. Returns the timeline value the caller's submit must SIGNAL on
    /// Semaphore(); 0 (nothing recorded) when not configured, the bridge is broken, or the size is
    /// zero or above the configured maximum.
    uint64_t RecordInputs(VkCommandBuffer cmd, VkImage color, VkImage depth, VkImage motion,
                          uint32_t render_w, uint32_t render_h);

    /// D3D12 waits for `inputs_ready` on the GPU, runs ffxDispatch and signals. Returns the value
    /// the caller's next submit must WAIT on, or 0 on error. `inputs_ready` must be a value
    /// RecordInputs returned. After a failed Signal or Wait, or a removed device, the bridge is
    /// broken: every later Dispatch and Configure fails and RecordInputs returns 0.
    uint64_t Dispatch(uint64_t inputs_ready, const FrameParams& params, std::string* error);

    /// Acquires the shared output and copies it into `output` (left in GENERAL). Records nothing
    /// before the first RecordInputs after Configure.
    void RecordOutput(VkCommandBuffer cmd, VkImage output);

    VkSemaphore Semaphore() const;

    /// Waits (bounded) for D3D12's last dispatch, then releases everything. If that wait fails (or the
    /// bridge is broken) the FFX context, the D3D12 objects and the shared Vulkan objects are leaked
    /// on purpose, since the GPU may still use them. The DLL stays loaded.
    void Destroy();

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};

} // namespace Vulkan::HelixSr
