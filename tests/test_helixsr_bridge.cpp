// SPDX-License-Identifier: GPL-2.0-or-later
//
// Round trip through the HelixSR bridge with nothing else of the port: Vulkan images -> D3D12 ->
// ffxDispatch of an FFX-API upscaler DLL -> Vulkan image. The input is a colour gradient, so a
// working upscaler returns the gradient at 1920x1080 and a network that did not run (HelixSR draws
// a red border then) shows up in the points 2 px from each edge.
//
//   ninja -C out/gpu helixsr-bridge-test
//   out/gpu/helixsr-bridge-test.exe <absolute path of an FFX DX12 DLL>
//
// Exit 0 and "PASS ..." when the output matches, exit 1 and "FAIL <reason>" otherwise.

#define VK_NO_PROTOTYPES
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <vulkan/vulkan_core.h>
#include <vulkan/vulkan_win32.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

#include "video_core/renderer_vulkan/vk_helixsr.h"

namespace {

constexpr uint32_t RenderW = 1280, RenderH = 720, OutW = 1920, OutH = 1080;
constexpr uint32_t Frames = 16;
constexpr float Tolerance = 0.06f;

[[noreturn]] void Fail(const std::string& reason) {
    std::printf("FAIL %s\n", reason.c_str());
    std::fflush(stdout);
    std::exit(1);
}

void Check(VkResult result, const char* what) {
    if (result != VK_SUCCESS) {
        Fail(std::string{what} + " returned VkResult " + std::to_string(static_cast<int>(result)));
    }
}

uint16_t ToHalf(float value) {
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    const uint32_t sign = (bits >> 16) & 0x8000u;
    const int exponent = static_cast<int>((bits >> 23) & 0xff) - 127 + 15;
    if (exponent <= 0) {
        return static_cast<uint16_t>(sign); // denormals are not needed for 0..1
    }
    if (exponent >= 31) {
        return static_cast<uint16_t>(sign | 0x7c00u);
    }
    return static_cast<uint16_t>(sign | (exponent << 10) | ((bits & 0x7fffffu) >> 13));
}

float FromHalf(uint16_t half) {
    const float sign = (half & 0x8000u) ? -1.0f : 1.0f;
    const int exponent = (half >> 10) & 0x1f;
    const int mantissa = half & 0x3ff;
    if (exponent == 31) {
        return mantissa ? std::nanf("") : sign * INFINITY;
    }
    if (exponent == 0) {
        return sign * std::ldexp(static_cast<float>(mantissa), -24);
    }
    return sign * std::ldexp(static_cast<float>(mantissa | 0x400), exponent - 25);
}

float Halton(uint32_t index, uint32_t base) {
    float result = 0.0f, fraction = 1.0f;
    while (index > 0) {
        fraction /= static_cast<float>(base);
        result += fraction * static_cast<float>(index % base);
        index /= base;
    }
    return result;
}

#define DEVICE_FUNCTIONS(X)                                                                        \
    X(vkGetDeviceQueue) X(vkCreateCommandPool) X(vkAllocateCommandBuffers) X(vkBeginCommandBuffer) \
    X(vkEndCommandBuffer) X(vkResetCommandBuffer) X(vkQueueSubmit) X(vkCreateFence)               \
    X(vkWaitForFences) X(vkResetFences) X(vkCreateImage) X(vkGetImageMemoryRequirements)          \
    X(vkAllocateMemory) X(vkBindImageMemory) X(vkCreateBuffer) X(vkGetBufferMemoryRequirements)   \
    X(vkBindBufferMemory) X(vkMapMemory) X(vkUnmapMemory) X(vkCmdPipelineBarrier)                 \
    X(vkCmdClearColorImage) X(vkCmdClearDepthStencilImage) X(vkCmdCopyBufferToImage)              \
    X(vkCmdCopyImageToBuffer) X(vkQueueWaitIdle) X(vkDeviceWaitIdle)

struct DeviceFunctions {
#define X(name) PFN_##name name{};
    DEVICE_FUNCTIONS(X)
#undef X
};

struct Image {
    VkImage image{};
    VkImageAspectFlags aspect{VK_IMAGE_ASPECT_COLOR_BIT};
};

struct Buffer {
    VkBuffer buffer{};
    VkDeviceMemory memory{};
    void* mapped{};
};

struct Context {
    HMODULE vulkan_dll{};
    PFN_vkGetInstanceProcAddr get_instance_proc{};
    VkInstance instance{};
    VkPhysicalDevice physical{};
    VkDevice device{};
    VkQueue queue{};
    uint32_t family{};
    uint8_t luid[8]{};
    DeviceFunctions vk;
    VkPhysicalDeviceMemoryProperties memory{};
    VkCommandPool pool{};
    VkFence fence{};

    uint32_t FindMemory(uint32_t bits, VkMemoryPropertyFlags flags) const {
        for (uint32_t i = 0; i < memory.memoryTypeCount; ++i) {
            if ((bits & (1u << i)) && (memory.memoryTypes[i].propertyFlags & flags) == flags) {
                return i;
            }
        }
        Fail("no suitable Vulkan memory type");
    }

    Image MakeImage(VkFormat format, uint32_t width, uint32_t height, VkImageUsageFlags usage,
                    VkImageAspectFlags aspect) {
        VkImageCreateInfo info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
        info.imageType = VK_IMAGE_TYPE_2D;
        info.format = format;
        info.extent = {width, height, 1};
        info.mipLevels = 1;
        info.arrayLayers = 1;
        info.samples = VK_SAMPLE_COUNT_1_BIT;
        info.tiling = VK_IMAGE_TILING_OPTIMAL;
        info.usage = usage;
        info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
        Image result{};
        result.aspect = aspect;
        Check(vk.vkCreateImage(device, &info, nullptr, &result.image), "vkCreateImage");
        VkMemoryRequirements requirements;
        vk.vkGetImageMemoryRequirements(device, result.image, &requirements);
        VkMemoryAllocateInfo allocate{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
        allocate.allocationSize = requirements.size;
        allocate.memoryTypeIndex =
            FindMemory(requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        VkDeviceMemory memory_object;
        Check(vk.vkAllocateMemory(device, &allocate, nullptr, &memory_object), "vkAllocateMemory");
        Check(vk.vkBindImageMemory(device, result.image, memory_object, 0), "vkBindImageMemory");
        return result;
    }

    Buffer MakeHostBuffer(VkDeviceSize size) {
        VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
        info.size = size;
        info.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        Buffer result{};
        Check(vk.vkCreateBuffer(device, &info, nullptr, &result.buffer), "vkCreateBuffer");
        VkMemoryRequirements requirements;
        vk.vkGetBufferMemoryRequirements(device, result.buffer, &requirements);
        VkMemoryAllocateInfo allocate{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
        allocate.allocationSize = requirements.size;
        allocate.memoryTypeIndex =
            FindMemory(requirements.memoryTypeBits,
                       VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        Check(vk.vkAllocateMemory(device, &allocate, nullptr, &result.memory), "vkAllocateMemory (host)");
        Check(vk.vkBindBufferMemory(device, result.buffer, result.memory, 0), "vkBindBufferMemory");
        Check(vk.vkMapMemory(device, result.memory, 0, VK_WHOLE_SIZE, 0, &result.mapped), "vkMapMemory");
        return result;
    }

    VkCommandBuffer MakeCommandBuffer() {
        VkCommandBufferAllocateInfo info{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
        info.commandPool = pool;
        info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        info.commandBufferCount = 1;
        VkCommandBuffer cmd;
        Check(vk.vkAllocateCommandBuffers(device, &info, &cmd), "vkAllocateCommandBuffers");
        return cmd;
    }

    void Begin(VkCommandBuffer cmd) {
        Check(vk.vkResetCommandBuffer(cmd, 0), "vkResetCommandBuffer");
        VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        Check(vk.vkBeginCommandBuffer(cmd, &begin), "vkBeginCommandBuffer");
    }

    /// Submits `cmd`, optionally waiting on / signalling a timeline value, and waits (bounded) for it.
    void Submit(VkCommandBuffer cmd, VkSemaphore semaphore, uint64_t wait_value, uint64_t signal_value,
                bool wait_for_completion) {
        Check(vk.vkEndCommandBuffer(cmd), "vkEndCommandBuffer");
        VkTimelineSemaphoreSubmitInfo timeline{VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO};
        const VkPipelineStageFlags stage = VK_PIPELINE_STAGE_ALL_COMMANDS_BIT;
        VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        submit.commandBufferCount = 1;
        submit.pCommandBuffers = &cmd;
        if (wait_value) {
            timeline.waitSemaphoreValueCount = 1;
            timeline.pWaitSemaphoreValues = &wait_value;
            submit.waitSemaphoreCount = 1;
            submit.pWaitSemaphores = &semaphore;
            submit.pWaitDstStageMask = &stage;
        }
        if (signal_value) {
            timeline.signalSemaphoreValueCount = 1;
            timeline.pSignalSemaphoreValues = &signal_value;
            submit.signalSemaphoreCount = 1;
            submit.pSignalSemaphores = &semaphore;
        }
        submit.pNext = &timeline;
        Check(vk.vkQueueSubmit(queue, 1, &submit, wait_for_completion ? fence : VK_NULL_HANDLE),
              "vkQueueSubmit");
        if (wait_for_completion) {
            const VkResult result = vk.vkWaitForFences(device, 1, &fence, VK_TRUE, 10'000'000'000ull);
            if (result == VK_TIMEOUT) {
                Fail("the GPU did not finish within 10 s (the D3D12 side never signalled?)");
            }
            Check(result, "vkWaitForFences");
            Check(vk.vkResetFences(device, 1, &fence), "vkResetFences");
        }
    }

    void ToGeneral(VkCommandBuffer cmd, const std::vector<Image>& images) {
        std::vector<VkImageMemoryBarrier> barriers;
        for (const Image& image : images) {
            VkImageMemoryBarrier barrier{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
            barrier.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
            barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
            barrier.srcQueueFamilyIndex = barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.image = image.image;
            barrier.subresourceRange = {image.aspect, 0, 1, 0, 1};
            barriers.push_back(barrier);
        }
        vk.vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0,
                                0, nullptr, 0, nullptr, static_cast<uint32_t>(barriers.size()),
                                barriers.data());
    }
};

void CreateVulkan(Context& ctx) {
    ctx.vulkan_dll = LoadLibraryW(L"vulkan-1.dll");
    if (!ctx.vulkan_dll) {
        Fail("vulkan-1.dll is not available");
    }
    ctx.get_instance_proc = reinterpret_cast<PFN_vkGetInstanceProcAddr>(
        GetProcAddress(ctx.vulkan_dll, "vkGetInstanceProcAddr"));
    const auto create_instance = reinterpret_cast<PFN_vkCreateInstance>(
        ctx.get_instance_proc(nullptr, "vkCreateInstance"));
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = "helixsr-bridge-test";
    app.apiVersion = VK_API_VERSION_1_3;
    VkInstanceCreateInfo instance_info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    instance_info.pApplicationInfo = &app;
    Check(create_instance(&instance_info, nullptr, &ctx.instance), "vkCreateInstance");

    const auto instance_fn = [&](const char* name) { return ctx.get_instance_proc(ctx.instance, name); };
    const auto enumerate = reinterpret_cast<PFN_vkEnumeratePhysicalDevices>(instance_fn("vkEnumeratePhysicalDevices"));
    const auto get_properties = reinterpret_cast<PFN_vkGetPhysicalDeviceProperties>(instance_fn("vkGetPhysicalDeviceProperties"));
    const auto get_properties2 = reinterpret_cast<PFN_vkGetPhysicalDeviceProperties2>(instance_fn("vkGetPhysicalDeviceProperties2"));
    const auto get_families = reinterpret_cast<PFN_vkGetPhysicalDeviceQueueFamilyProperties>(instance_fn("vkGetPhysicalDeviceQueueFamilyProperties"));
    const auto get_memory = reinterpret_cast<PFN_vkGetPhysicalDeviceMemoryProperties>(instance_fn("vkGetPhysicalDeviceMemoryProperties"));
    const auto enumerate_extensions = reinterpret_cast<PFN_vkEnumerateDeviceExtensionProperties>(instance_fn("vkEnumerateDeviceExtensionProperties"));
    const auto create_device = reinterpret_cast<PFN_vkCreateDevice>(instance_fn("vkCreateDevice"));

    uint32_t count = 0;
    Check(enumerate(ctx.instance, &count, nullptr), "vkEnumeratePhysicalDevices");
    std::vector<VkPhysicalDevice> devices(count);
    Check(enumerate(ctx.instance, &count, devices.data()), "vkEnumeratePhysicalDevices");
    if (devices.empty()) {
        Fail("no Vulkan device");
    }
    ctx.physical = devices[0];
    for (VkPhysicalDevice device : devices) {
        VkPhysicalDeviceProperties props;
        get_properties(device, &props);
        if (props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) {
            ctx.physical = device;
            break;
        }
    }

    VkPhysicalDeviceIDProperties id{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES};
    VkPhysicalDeviceProperties2 props2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
    props2.pNext = &id;
    get_properties2(ctx.physical, &props2);
    if (!id.deviceLUIDValid) {
        Fail("the Vulkan device reports no LUID");
    }
    std::memcpy(ctx.luid, id.deviceLUID, sizeof(ctx.luid));
    std::printf("device: %s\n", props2.properties.deviceName);

    uint32_t family_count = 0;
    get_families(ctx.physical, &family_count, nullptr);
    std::vector<VkQueueFamilyProperties> families(family_count);
    get_families(ctx.physical, &family_count, families.data());
    bool found = false;
    for (uint32_t i = 0; i < family_count && !found; ++i) {
        if (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) {
            ctx.family = i;
            found = true;
        }
    }
    if (!found) {
        Fail("no graphics queue family");
    }

    const char* extensions[] = {VK_KHR_EXTERNAL_MEMORY_WIN32_EXTENSION_NAME,
                                VK_KHR_EXTERNAL_SEMAPHORE_WIN32_EXTENSION_NAME};
    uint32_t extension_count = 0;
    enumerate_extensions(ctx.physical, nullptr, &extension_count, nullptr);
    std::vector<VkExtensionProperties> available(extension_count);
    enumerate_extensions(ctx.physical, nullptr, &extension_count, available.data());
    for (const char* wanted : extensions) {
        const bool present = std::any_of(available.begin(), available.end(), [&](const auto& e) {
            return std::strcmp(e.extensionName, wanted) == 0;
        });
        if (!present) {
            Fail(std::string{"the device lacks "} + wanted);
        }
    }

    const float priority = 1.0f;
    VkDeviceQueueCreateInfo queue_info{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    queue_info.queueFamilyIndex = ctx.family;
    queue_info.queueCount = 1;
    queue_info.pQueuePriorities = &priority;
    VkPhysicalDeviceVulkan12Features features12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
    features12.timelineSemaphore = VK_TRUE;
    VkDeviceCreateInfo device_info{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    device_info.pNext = &features12;
    device_info.queueCreateInfoCount = 1;
    device_info.pQueueCreateInfos = &queue_info;
    device_info.enabledExtensionCount = 2;
    device_info.ppEnabledExtensionNames = extensions;
    Check(create_device(ctx.physical, &device_info, nullptr, &ctx.device), "vkCreateDevice");
    get_memory(ctx.physical, &ctx.memory);

    const auto get_device_proc = reinterpret_cast<PFN_vkGetDeviceProcAddr>(instance_fn("vkGetDeviceProcAddr"));
#define X(name)                                                                                    \
    ctx.vk.name = reinterpret_cast<PFN_##name>(get_device_proc(ctx.device, #name));                \
    if (!ctx.vk.name) {                                                                            \
        Fail("missing Vulkan function " #name);                                                    \
    }
    DEVICE_FUNCTIONS(X)
#undef X
    ctx.vk.vkGetDeviceQueue(ctx.device, ctx.family, 0, &ctx.queue);

    VkCommandPoolCreateInfo pool_info{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    pool_info.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    pool_info.queueFamilyIndex = ctx.family;
    Check(ctx.vk.vkCreateCommandPool(ctx.device, &pool_info, nullptr, &ctx.pool), "vkCreateCommandPool");
    VkFenceCreateInfo fence_info{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    Check(ctx.vk.vkCreateFence(ctx.device, &fence_info, nullptr, &ctx.fence), "vkCreateFence");
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::printf("usage: helixsr-bridge-test <absolute path of an FFX DX12 DLL>\n");
        return 2;
    }
    const std::filesystem::path dll{argv[1]};
    if (!dll.is_absolute() || !std::filesystem::is_regular_file(dll)) {
        Fail("the DLL path must be an absolute path of an existing file");
    }
    const std::string dll_name = dll.filename().string();

    Context ctx;
    CreateVulkan(ctx);

    // The game's images, all left in GENERAL.
    constexpr VkImageUsageFlags transfer = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    const Image color = ctx.MakeImage(VK_FORMAT_R16G16B16A16_SFLOAT, RenderW, RenderH, transfer,
                                      VK_IMAGE_ASPECT_COLOR_BIT);
    const Image depth = ctx.MakeImage(VK_FORMAT_D32_SFLOAT_S8_UINT, RenderW, RenderH, transfer,
                                      VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT);
    const Image motion = ctx.MakeImage(VK_FORMAT_R16G16_SFLOAT, RenderW, RenderH, transfer,
                                       VK_IMAGE_ASPECT_COLOR_BIT);
    const Image output = ctx.MakeImage(
        VK_FORMAT_R16G16B16A16_SFLOAT, OutW, OutH,
        transfer | VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT, VK_IMAGE_ASPECT_COLOR_BIT);

    const Buffer staging = ctx.MakeHostBuffer(VkDeviceSize{RenderW} * RenderH * 8);
    auto* texels = static_cast<uint16_t*>(staging.mapped);
    for (uint32_t y = 0; y < RenderH; ++y) {
        for (uint32_t x = 0; x < RenderW; ++x) {
            uint16_t* texel = texels + (size_t{y} * RenderW + x) * 4;
            texel[0] = ToHalf((x + 0.5f) / RenderW);
            texel[1] = ToHalf((y + 0.5f) / RenderH);
            texel[2] = ToHalf(0.5f);
            texel[3] = ToHalf(1.0f);
        }
    }
    const Buffer readback = ctx.MakeHostBuffer(VkDeviceSize{OutW} * OutH * 8);

    VkCommandBuffer setup = ctx.MakeCommandBuffer();
    ctx.Begin(setup);
    ctx.ToGeneral(setup, {color, depth, motion, output});
    VkBufferImageCopy upload{};
    upload.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    upload.imageExtent = {RenderW, RenderH, 1};
    ctx.vk.vkCmdCopyBufferToImage(setup, staging.buffer, color.image, VK_IMAGE_LAYOUT_GENERAL, 1, &upload);
    const VkClearDepthStencilValue depth_value{0.5f, 0};
    const VkImageSubresourceRange depth_range{VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT, 0, 1,
                                              0, 1};
    ctx.vk.vkCmdClearDepthStencilImage(setup, depth.image, VK_IMAGE_LAYOUT_GENERAL, &depth_value, 1,
                                       &depth_range);
    const VkClearColorValue zero{};
    const VkImageSubresourceRange color_range{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    ctx.vk.vkCmdClearColorImage(setup, motion.image, VK_IMAGE_LAYOUT_GENERAL, &zero, 1, &color_range);
    ctx.Submit(setup, VK_NULL_HANDLE, 0, 0, true);

    Vulkan::HelixSr::DeviceInfo info;
    info.instance = ctx.instance;
    info.physical = ctx.physical;
    info.device = ctx.device;
    info.queue_family = ctx.family;
    info.get_instance_proc = ctx.get_instance_proc;
    info.get_device_proc = reinterpret_cast<PFN_vkGetDeviceProcAddr>(
        ctx.get_instance_proc(ctx.instance, "vkGetDeviceProcAddr"));
    std::memcpy(info.luid, ctx.luid, sizeof(info.luid));

    Vulkan::HelixSr::Bridge bridge;
    std::string error;
    if (!bridge.Init(info, dll.c_str(), &error)) {
        Fail("Init: " + error);
    }
    // HIGH_DYNAMIC_RANGE | AUTO_EXPOSURE (FfxApiCreateContextUpscaleFlags).
    constexpr uint32_t CreateFlags = (1u << 0) | (1u << 5);
    if (!bridge.Configure(RenderW, RenderH, OutW, OutH, VK_FORMAT_R16G16B16A16_SFLOAT, CreateFlags, &error)) {
        Fail("Configure: " + error);
    }

    VkCommandBuffer inputs_cmd = ctx.MakeCommandBuffer();
    VkCommandBuffer output_cmd = ctx.MakeCommandBuffer();
    for (uint32_t frame = 0; frame < Frames; ++frame) {
        ctx.Begin(inputs_cmd);
        const uint64_t inputs_ready =
            bridge.RecordInputs(inputs_cmd, color.image, depth.image, motion.image, RenderW, RenderH);
        if (!inputs_ready) {
            Fail("RecordInputs returned 0");
        }
        ctx.Submit(inputs_cmd, bridge.Semaphore(), 0, inputs_ready, false);

        Vulkan::HelixSr::FrameParams params;
        params.jitter_x = Halton(frame % 18 + 1, 2) - 0.5f;
        params.jitter_y = Halton(frame % 18 + 1, 3) - 0.5f;
        params.render_w = RenderW;
        params.render_h = RenderH;
        params.frame_ms = 16.6f;
        params.pre_exposure = 1.0f;
        params.near_plane = 0.05f;
        params.far_plane = 3000.0f;
        params.fov_y = 1.0f;
        params.reset = frame == 0;
        const uint64_t done = bridge.Dispatch(inputs_ready, params, &error);
        if (!done) {
            Fail("Dispatch (frame " + std::to_string(frame) + "): " + error);
        }

        ctx.Begin(output_cmd);
        bridge.RecordOutput(output_cmd, output.image);
        if (frame + 1 == Frames) {
            VkBufferImageCopy download{};
            download.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
            download.imageExtent = {OutW, OutH, 1};
            ctx.vk.vkCmdCopyImageToBuffer(output_cmd, output.image, VK_IMAGE_LAYOUT_GENERAL,
                                          readback.buffer, 1, &download);
            VkMemoryBarrier to_host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
            to_host.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            to_host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
            ctx.vk.vkCmdPipelineBarrier(output_cmd, VK_PIPELINE_STAGE_TRANSFER_BIT,
                                        VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &to_host, 0, nullptr, 0, nullptr);
        }
        ctx.Submit(output_cmd, bridge.Semaphore(), done, 0, true);
    }

    const auto* pixels = static_cast<const uint16_t*>(readback.mapped);
    for (size_t i = 0; i < size_t{OutW} * OutH * 4; ++i) {
        if (i % 4 != 3 && !std::isfinite(FromHalf(pixels[i]))) {
            Fail("the output holds NaN or Inf (texel " + std::to_string(i / 4) + ")");
        }
    }
    float max_error = 0.0f;
    for (uint32_t gy = 0; gy < 9; ++gy) {
        for (uint32_t gx = 0; gx < 9; ++gx) {
            const uint32_t x = 2 + gx * (OutW - 5) / 8;
            const uint32_t y = 2 + gy * (OutH - 5) / 8;
            const uint16_t* texel = pixels + (size_t{y} * OutW + x) * 4;
            const float expected[3] = {(x + 0.5f) / OutW, (y + 0.5f) / OutH, 0.5f};
            for (int c = 0; c < 3; ++c) {
                const float error_c = std::fabs(FromHalf(texel[c]) - expected[c]);
                max_error = std::max(max_error, error_c);
                if (error_c > Tolerance) {
                    char text[160];
                    std::snprintf(text, sizeof(text),
                                  "output (%u,%u) channel %d is %.4f, expected %.4f (tolerance %.2f)", x, y,
                                  c, FromHalf(texel[c]), expected[c], Tolerance);
                    Fail(text);
                }
            }
        }
    }

    bridge.Destroy();
    std::printf("PASS %s %ux%u->%ux%u max_err=%.4f\n", dll_name.c_str(), RenderW, RenderH, OutW, OutH,
                max_error);
    return 0;
}
