// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstdio>
#include <cstring>

#include "video_core/renderer_vulkan/vk_helixsr.h"

#ifdef _WIN32

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <d3d12.h>
#include <dxgi1_4.h>
#include <vulkan/vulkan_win32.h>

#include "dx12/ffx_api_dx12.h"
#include "ffx_api.h"
#include "ffx_api_types.h"
#include "ffx_upscale.h"

namespace Vulkan::HelixSr {

namespace {

constexpr uint32_t AllocatorCount = 3;
constexpr DWORD WaitTimeoutMs = 5000;

template <typename T>
class Com {
public:
    Com() = default;
    Com(const Com&) = delete;
    Com& operator=(const Com&) = delete;
    ~Com() {
        Reset();
    }
    void Reset() {
        if (ptr) {
            ptr->Release();
            ptr = nullptr;
        }
    }
    /// Drops the pointer without releasing it: the object may still be executing on the GPU.
    void Leak() {
        ptr = nullptr;
    }
    T** Put() {
        Reset();
        return &ptr;
    }
    T* Get() const {
        return ptr;
    }
    T* operator->() const {
        return ptr;
    }
    explicit operator bool() const {
        return ptr != nullptr;
    }

private:
    T* ptr{};
};

/// The Vulkan entry points the bridge calls, resolved through the caller's procedure getters.
struct VkApi {
    PFN_vkGetPhysicalDeviceExternalSemaphoreProperties get_external_semaphore_properties;
    PFN_vkGetPhysicalDeviceImageFormatProperties2 get_image_format_properties;
    PFN_vkGetPhysicalDeviceMemoryProperties get_memory_properties;
    PFN_vkCreateSemaphore create_semaphore;
    PFN_vkDestroySemaphore destroy_semaphore;
    PFN_vkImportSemaphoreWin32HandleKHR import_semaphore;
    PFN_vkCreateImage create_image;
    PFN_vkDestroyImage destroy_image;
    PFN_vkGetImageMemoryRequirements2 get_image_requirements;
    PFN_vkGetMemoryWin32HandlePropertiesKHR get_handle_properties;
    PFN_vkAllocateMemory allocate_memory;
    PFN_vkFreeMemory free_memory;
    PFN_vkBindImageMemory bind_image_memory;
    PFN_vkCreateBuffer create_buffer;
    PFN_vkDestroyBuffer destroy_buffer;
    PFN_vkGetBufferMemoryRequirements get_buffer_requirements;
    PFN_vkBindBufferMemory bind_buffer_memory;
    PFN_vkCmdPipelineBarrier cmd_pipeline_barrier;
    PFN_vkCmdCopyImage cmd_copy_image;
    PFN_vkCmdCopyImageToBuffer cmd_copy_image_to_buffer;
    PFN_vkCmdCopyBufferToImage cmd_copy_buffer_to_image;
};

struct FormatInfo {
    VkFormat game;   ///< what the renderer's image uses
    VkFormat shared; ///< the Vulkan image bound to the D3D12 resource
    DXGI_FORMAT dxgi;
    uint32_t ffx;
    const char* name;
};

// R8G8B8A8_SRGB maps to the UNORM format on the D3D12 side: the copy moves raw texels, and a
// non-typeless D3D12 resource cannot be imported under a different format.
constexpr std::array<FormatInfo, 7> Formats{{
    {VK_FORMAT_R16G16B16A16_SFLOAT, VK_FORMAT_R16G16B16A16_SFLOAT, DXGI_FORMAT_R16G16B16A16_FLOAT,
     FFX_API_SURFACE_FORMAT_R16G16B16A16_FLOAT, "R16G16B16A16_SFLOAT"},
    {VK_FORMAT_R8G8B8A8_UNORM, VK_FORMAT_R8G8B8A8_UNORM, DXGI_FORMAT_R8G8B8A8_UNORM,
     FFX_API_SURFACE_FORMAT_R8G8B8A8_UNORM, "R8G8B8A8_UNORM"},
    {VK_FORMAT_R8G8B8A8_SRGB, VK_FORMAT_R8G8B8A8_UNORM, DXGI_FORMAT_R8G8B8A8_UNORM,
     FFX_API_SURFACE_FORMAT_R8G8B8A8_UNORM, "R8G8B8A8_SRGB"},
    {VK_FORMAT_B10G11R11_UFLOAT_PACK32, VK_FORMAT_B10G11R11_UFLOAT_PACK32,
     DXGI_FORMAT_R11G11B10_FLOAT, FFX_API_SURFACE_FORMAT_R11G11B10_FLOAT, "B10G11R11_UFLOAT_PACK32"},
    {VK_FORMAT_A2B10G10R10_UNORM_PACK32, VK_FORMAT_A2B10G10R10_UNORM_PACK32,
     DXGI_FORMAT_R10G10B10A2_UNORM, FFX_API_SURFACE_FORMAT_R10G10B10A2_UNORM,
     "A2B10G10R10_UNORM_PACK32"},
    {VK_FORMAT_R16G16_SFLOAT, VK_FORMAT_R16G16_SFLOAT, DXGI_FORMAT_R16G16_FLOAT,
     FFX_API_SURFACE_FORMAT_R16G16_FLOAT, "R16G16_SFLOAT"},
    {VK_FORMAT_R32_SFLOAT, VK_FORMAT_R32_SFLOAT, DXGI_FORMAT_R32_FLOAT,
     FFX_API_SURFACE_FORMAT_R32_FLOAT, "R32_SFLOAT"},
}};

const FormatInfo* FindFormat(VkFormat game) {
    for (const FormatInfo& info : Formats) {
        if (info.game == game) {
            return &info;
        }
    }
    return nullptr;
}

const char* FfxResultName(uint32_t code) {
    static constexpr std::array<const char*, 8> names{
        "ok",          "error",        "unknown descriptor type", "runtime error",
        "no provider", "out of memory", "invalid parameter",      "descriptor type too new"};
    return code < names.size() ? names[code] : "unknown";
}

void FfxMessage(uint32_t type, const wchar_t* text) {
    std::printf("HelixSR: %s: %ls\n", type == FFX_API_MESSAGE_TYPE_ERROR ? "error" : "warning",
                text ? text : L"");
}

std::string Hr(const char* what, HRESULT hr) {
    char code[16];
    std::snprintf(code, sizeof(code), "0x%08lx", static_cast<unsigned long>(hr));
    return std::string{what} + " failed (HRESULT " + code + ")";
}

std::string Vk(const char* what, VkResult result) {
    return std::string{what} + " failed (VkResult " + std::to_string(static_cast<int>(result)) + ")";
}

VkImageMemoryBarrier ImageBarrier(VkImage image, VkImageLayout old_layout, VkImageLayout new_layout,
                                  VkAccessFlags src_access, VkAccessFlags dst_access,
                                  uint32_t src_family, uint32_t dst_family) {
    VkImageMemoryBarrier barrier{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    barrier.srcAccessMask = src_access;
    barrier.dstAccessMask = dst_access;
    barrier.oldLayout = old_layout;
    barrier.newLayout = new_layout;
    barrier.srcQueueFamilyIndex = src_family;
    barrier.dstQueueFamilyIndex = dst_family;
    barrier.image = image;
    barrier.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    return barrier;
}

void Transition(D3D12_RESOURCE_BARRIER& barrier, ID3D12Resource* resource,
                D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
    barrier = {};
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition.pResource = resource;
    barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    barrier.Transition.StateBefore = before;
    barrier.Transition.StateAfter = after;
}

} // namespace

struct Bridge::Impl {
    /// One image created by D3D12 and imported into Vulkan.
    struct SharedImage {
        Com<ID3D12Resource> resource;
        VkImage image{};
        VkDeviceMemory memory{};
        VkImageLayout layout{VK_IMAGE_LAYOUT_UNDEFINED}; ///< as Vulkan last left it
        const FormatInfo* format{};
        uint32_t width{}, height{};
    };

    DeviceInfo info{};
    VkApi vk{};
    VkPhysicalDeviceMemoryProperties memory_properties{};

    // Modules are never freed, like the other FFX loaders in the renderer.
    HMODULE ffx_module{};
    PfnFfxCreateContext ffx_create{};
    PfnFfxDestroyContext ffx_destroy{};
    PfnFfxDispatch ffx_dispatch{};
    ffxContext context{};

    Com<IDXGIFactory4> factory;
    Com<ID3D12Device> device;
    Com<ID3D12CommandQueue> queue;
    std::array<Com<ID3D12CommandAllocator>, AllocatorCount> allocators;
    Com<ID3D12GraphicsCommandList> list;
    Com<ID3D12Fence> fence;
    HANDLE event{};
    VkSemaphore semaphore{};

    uint64_t counter{};       ///< last value handed out, for both directions
    uint64_t last_dispatch{}; ///< last value D3D12 signals
    std::array<uint64_t, AllocatorCount> slot_value{};
    uint64_t frame{};

    SharedImage color, depth, motion, output;
    VkBuffer depth_buffer{};
    VkDeviceMemory depth_memory{};
    uint32_t max_w{}, max_h{}, out_w{}, out_h{};
    bool logged_dispatch{};
    /// Set when the fence can no longer be trusted (a failed Signal or Wait, a removed device).
    /// Nothing is dispatched or reconfigured after that; Destroy() leaks instead of releasing.
    bool broken{};
    std::string broken_reason;

    ~Impl() {
        Destroy();
    }

    void Break(std::string reason) {
        broken = true;
        broken_reason = std::move(reason);
    }

    void Fault(std::string* error, std::string reason) {
        Break(reason);
        Fail(error, std::move(reason));
    }

    bool CheckNotBroken(std::string* error) const {
        return !broken || Fail(error, "the bridge is broken: " + broken_reason);
    }

    static bool Fail(std::string* error, std::string message) {
        std::printf("HelixSR: %s\n", message.c_str());
        if (error) {
            *error = std::move(message);
        }
        return false;
    }

    bool WaitFence(uint64_t value, std::string* error) {
        if (!fence) {
            return true;
        }
        const uint64_t completed = fence->GetCompletedValue();
        if (completed == UINT64_MAX) { // what D3D12 reports once the device is removed
            return Fail(error, "the D3D12 device was removed");
        }
        if (completed >= value) {
            return true;
        }
        if (const HRESULT hr = fence->SetEventOnCompletion(value, event); FAILED(hr)) {
            return Fail(error, Hr("SetEventOnCompletion", hr));
        }
        if (WaitForSingleObject(event, WaitTimeoutMs) != WAIT_OBJECT_0) {
            return Fail(error, "timed out waiting for the D3D12 fence (value " +
                                   std::to_string(value) + ")");
        }
        return true;
    }

    template <typename F>
    static bool Load(F& function, PFN_vkVoidFunction address, const char* name, std::string& missing) {
        function = reinterpret_cast<F>(address);
        if (!function) {
            missing += std::string{missing.empty() ? "" : ", "} + name;
        }
        return function != nullptr;
    }

    bool LoadVulkan(std::string* error) {
        std::string missing;
        const auto instance = [&](auto& function, const char* name) {
            Load(function, info.get_instance_proc(info.instance, name), name, missing);
        };
        const auto dev = [&](auto& function, const char* name) {
            Load(function, info.get_device_proc(info.device, name), name, missing);
        };
        instance(vk.get_external_semaphore_properties, "vkGetPhysicalDeviceExternalSemaphoreProperties");
        instance(vk.get_image_format_properties, "vkGetPhysicalDeviceImageFormatProperties2");
        instance(vk.get_memory_properties, "vkGetPhysicalDeviceMemoryProperties");
        dev(vk.create_semaphore, "vkCreateSemaphore");
        dev(vk.destroy_semaphore, "vkDestroySemaphore");
        dev(vk.import_semaphore, "vkImportSemaphoreWin32HandleKHR");
        dev(vk.create_image, "vkCreateImage");
        dev(vk.destroy_image, "vkDestroyImage");
        dev(vk.get_image_requirements, "vkGetImageMemoryRequirements2");
        dev(vk.get_handle_properties, "vkGetMemoryWin32HandlePropertiesKHR");
        dev(vk.allocate_memory, "vkAllocateMemory");
        dev(vk.free_memory, "vkFreeMemory");
        dev(vk.bind_image_memory, "vkBindImageMemory");
        dev(vk.create_buffer, "vkCreateBuffer");
        dev(vk.destroy_buffer, "vkDestroyBuffer");
        dev(vk.get_buffer_requirements, "vkGetBufferMemoryRequirements");
        dev(vk.bind_buffer_memory, "vkBindBufferMemory");
        dev(vk.cmd_pipeline_barrier, "vkCmdPipelineBarrier");
        dev(vk.cmd_copy_image, "vkCmdCopyImage");
        dev(vk.cmd_copy_image_to_buffer, "vkCmdCopyImageToBuffer");
        dev(vk.cmd_copy_buffer_to_image, "vkCmdCopyBufferToImage");
        if (!missing.empty()) {
            return Fail(error, "Vulkan functions not available (enable VK_KHR_external_memory_win32 and "
                               "VK_KHR_external_semaphore_win32): " + missing);
        }
        return true;
    }

    bool CheckSemaphoreSupport(std::string* error) {
        VkSemaphoreTypeCreateInfo type{VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO};
        type.semaphoreType = VK_SEMAPHORE_TYPE_TIMELINE;
        VkPhysicalDeviceExternalSemaphoreInfo query{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_SEMAPHORE_INFO};
        query.pNext = &type;
        query.handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_D3D12_FENCE_BIT;
        VkExternalSemaphoreProperties props{VK_STRUCTURE_TYPE_EXTERNAL_SEMAPHORE_PROPERTIES};
        vk.get_external_semaphore_properties(info.physical, &query, &props);
        if (!(props.externalSemaphoreFeatures & VK_EXTERNAL_SEMAPHORE_FEATURE_IMPORTABLE_BIT)) {
            return Fail(error, "the GPU cannot import a D3D12 fence as a Vulkan timeline semaphore");
        }
        return true;
    }

    bool Init(const DeviceInfo& device_info, const wchar_t* dll_path, std::string* error) {
        Destroy();
        info = device_info;
        if (!info.get_instance_proc || !info.get_device_proc || !info.device || !info.physical) {
            return Fail(error, "incomplete Vulkan device information");
        }
        if (!LoadVulkan(error) || !CheckSemaphoreSupport(error)) {
            return false;
        }
        vk.get_memory_properties(info.physical, &memory_properties);

        ffx_module = LoadLibraryExW(dll_path, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        if (!ffx_module) {
            return Fail(error, "cannot load the FFX DLL (error " + std::to_string(GetLastError()) + ")");
        }
        ffx_create = reinterpret_cast<PfnFfxCreateContext>(GetProcAddress(ffx_module, "ffxCreateContext"));
        ffx_destroy = reinterpret_cast<PfnFfxDestroyContext>(GetProcAddress(ffx_module, "ffxDestroyContext"));
        ffx_dispatch = reinterpret_cast<PfnFfxDispatch>(GetProcAddress(ffx_module, "ffxDispatch"));
        // ffxConfigure and ffxQuery are part of the API too; a DLL without them is not a provider.
        for (const char* name : {"ffxCreateContext", "ffxDestroyContext", "ffxConfigure", "ffxQuery",
                                 "ffxDispatch"}) {
            if (!GetProcAddress(ffx_module, name)) {
                return Fail(error, std::string{"the DLL is not an FFX API provider: it does not export "} +
                                       name);
            }
        }
        return CreateD3D12(error) && CreateSharedFence(error);
    }

    bool CreateD3D12(std::string* error) {
        using CreateDevice = PFN_D3D12_CREATE_DEVICE;
        using CreateFactory = HRESULT(WINAPI*)(UINT, REFIID, void**);
        const HMODULE d3d12 = LoadLibraryW(L"d3d12.dll");
        const HMODULE dxgi = LoadLibraryW(L"dxgi.dll");
        if (!d3d12 || !dxgi) {
            return Fail(error, "d3d12.dll or dxgi.dll is not available");
        }
        const auto create_device = reinterpret_cast<CreateDevice>(GetProcAddress(d3d12, "D3D12CreateDevice"));
        const auto create_factory = reinterpret_cast<CreateFactory>(GetProcAddress(dxgi, "CreateDXGIFactory2"));
        if (!create_device || !create_factory) {
            return Fail(error, "D3D12CreateDevice or CreateDXGIFactory2 is missing");
        }
        HRESULT hr = create_factory(0, __uuidof(IDXGIFactory4),
                                    reinterpret_cast<void**>(factory.Put()));
        if (FAILED(hr)) {
            return Fail(error, Hr("CreateDXGIFactory2", hr));
        }
        LUID luid;
        std::memcpy(&luid, info.luid, sizeof(luid));
        Com<IDXGIAdapter1> adapter;
        hr = factory->EnumAdapterByLuid(luid, __uuidof(IDXGIAdapter1),
                                        reinterpret_cast<void**>(adapter.Put()));
        if (FAILED(hr)) {
            return Fail(error, Hr("EnumAdapterByLuid (no D3D12 adapter matches the Vulkan device)", hr));
        }
        hr = create_device(adapter.Get(), D3D_FEATURE_LEVEL_12_0, __uuidof(ID3D12Device),
                           reinterpret_cast<void**>(device.Put()));
        if (FAILED(hr)) {
            hr = create_device(adapter.Get(), D3D_FEATURE_LEVEL_11_0, __uuidof(ID3D12Device),
                               reinterpret_cast<void**>(device.Put()));
        }
        if (FAILED(hr)) {
            return Fail(error, Hr("D3D12CreateDevice", hr));
        }
        D3D12_COMMAND_QUEUE_DESC queue_desc{};
        queue_desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
        hr = device->CreateCommandQueue(&queue_desc, __uuidof(ID3D12CommandQueue),
                                        reinterpret_cast<void**>(queue.Put()));
        if (FAILED(hr)) {
            return Fail(error, Hr("CreateCommandQueue", hr));
        }
        for (auto& allocator : allocators) {
            hr = device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT,
                                                __uuidof(ID3D12CommandAllocator),
                                                reinterpret_cast<void**>(allocator.Put()));
            if (FAILED(hr)) {
                return Fail(error, Hr("CreateCommandAllocator", hr));
            }
        }
        hr = device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocators[0].Get(), nullptr,
                                       __uuidof(ID3D12GraphicsCommandList),
                                       reinterpret_cast<void**>(list.Put()));
        if (FAILED(hr)) {
            return Fail(error, Hr("CreateCommandList", hr));
        }
        list->Close();
        event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (!event) {
            return Fail(error, "CreateEvent failed");
        }
        DXGI_ADAPTER_DESC1 desc{};
        adapter->GetDesc1(&desc);
        std::printf("HelixSR: D3D12 device on %ls\n", desc.Description);
        return true;
    }

    bool CreateSharedFence(std::string* error) {
        HRESULT hr = device->CreateFence(0, D3D12_FENCE_FLAG_SHARED, __uuidof(ID3D12Fence),
                                         reinterpret_cast<void**>(fence.Put()));
        if (FAILED(hr)) {
            return Fail(error, Hr("CreateFence", hr));
        }
        HANDLE handle{};
        hr = device->CreateSharedHandle(fence.Get(), nullptr, GENERIC_ALL, nullptr, &handle);
        if (FAILED(hr)) {
            return Fail(error, Hr("CreateSharedHandle (fence)", hr));
        }
        VkSemaphoreTypeCreateInfo type{VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO};
        type.semaphoreType = VK_SEMAPHORE_TYPE_TIMELINE;
        VkSemaphoreCreateInfo create{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        create.pNext = &type;
        VkResult result = vk.create_semaphore(info.device, &create, nullptr, &semaphore);
        if (result == VK_SUCCESS) {
            VkImportSemaphoreWin32HandleInfoKHR import{VK_STRUCTURE_TYPE_IMPORT_SEMAPHORE_WIN32_HANDLE_INFO_KHR};
            import.semaphore = semaphore;
            import.handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_D3D12_FENCE_BIT;
            import.handle = handle;
            result = vk.import_semaphore(info.device, &import);
        }
        // An NT handle stays owned by us: Vulkan only duplicates what it needs.
        CloseHandle(handle);
        if (result != VK_SUCCESS) {
            return Fail(error, Vk("importing the D3D12 fence into a timeline semaphore", result));
        }
        return true;
    }

    bool PickMemoryType(uint32_t allowed_bits, uint32_t& index) const {
        bool found = false;
        for (uint32_t i = 0; i < memory_properties.memoryTypeCount; ++i) {
            if (!(allowed_bits & (1u << i))) {
                continue;
            }
            if (memory_properties.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) {
                index = i;
                return true;
            }
            if (!found) {
                index = i;
                found = true;
            }
        }
        return found;
    }

    bool CheckImportable(const FormatInfo& format, VkImageUsageFlags usage, std::string* error) {
        VkPhysicalDeviceExternalImageFormatInfo external{
            VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO};
        external.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D12_RESOURCE_BIT;
        VkPhysicalDeviceImageFormatInfo2 query{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2};
        query.pNext = &external;
        query.format = format.shared;
        query.type = VK_IMAGE_TYPE_2D;
        query.tiling = VK_IMAGE_TILING_OPTIMAL;
        query.usage = usage;
        VkExternalImageFormatProperties external_props{VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES};
        VkImageFormatProperties2 props{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2};
        props.pNext = &external_props;
        const VkResult result = vk.get_image_format_properties(info.physical, &query, &props);
        if (result != VK_SUCCESS ||
            !(external_props.externalMemoryProperties.externalMemoryFeatures &
              VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT)) {
            return Fail(error, std::string{"the GPU cannot import a D3D12 texture as "} + format.name +
                                   " with the usage the bridge needs");
        }
        return true;
    }

    bool CreateShared(SharedImage& shared, VkFormat game_format, uint32_t width, uint32_t height,
                      VkImageUsageFlags usage, bool unordered_access, std::string* error) {
        const FormatInfo* format = FindFormat(game_format);
        if (!format) {
            return Fail(error, "unsupported format " + std::to_string(static_cast<int>(game_format)));
        }
        if (!CheckImportable(*format, usage, error)) {
            return false;
        }
        shared.format = format;
        shared.width = width;
        shared.height = height;

        D3D12_HEAP_PROPERTIES heap{};
        heap.Type = D3D12_HEAP_TYPE_DEFAULT;
        D3D12_RESOURCE_DESC desc{};
        desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        desc.Width = width;
        desc.Height = height;
        desc.DepthOrArraySize = 1;
        desc.MipLevels = 1;
        desc.Format = format->dxgi;
        desc.SampleDesc.Count = 1;
        desc.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
        desc.Flags = unordered_access ? D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS
                                      : D3D12_RESOURCE_FLAG_NONE;
        HRESULT hr = device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_SHARED, &desc,
                                                     D3D12_RESOURCE_STATE_COMMON, nullptr,
                                                     __uuidof(ID3D12Resource),
                                                     reinterpret_cast<void**>(shared.resource.Put()));
        if (FAILED(hr)) {
            return Fail(error, Hr("CreateCommittedResource", hr));
        }
        HANDLE handle{};
        hr = device->CreateSharedHandle(shared.resource.Get(), nullptr, GENERIC_ALL, nullptr, &handle);
        if (FAILED(hr)) {
            return Fail(error, Hr("CreateSharedHandle (image)", hr));
        }
        const bool imported = ImportImage(shared, handle, usage, error);
        CloseHandle(handle);
        return imported;
    }

    bool ImportImage(SharedImage& shared, HANDLE handle, VkImageUsageFlags usage, std::string* error) {
        VkExternalMemoryImageCreateInfo external{VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO};
        external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D12_RESOURCE_BIT;
        VkImageCreateInfo create{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
        create.pNext = &external;
        create.imageType = VK_IMAGE_TYPE_2D;
        create.format = shared.format->shared;
        create.extent = {shared.width, shared.height, 1};
        create.mipLevels = 1;
        create.arrayLayers = 1;
        create.samples = VK_SAMPLE_COUNT_1_BIT;
        create.tiling = VK_IMAGE_TILING_OPTIMAL;
        create.usage = usage;
        create.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        create.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
        VkResult result = vk.create_image(info.device, &create, nullptr, &shared.image);
        if (result != VK_SUCCESS) {
            return Fail(error, Vk("vkCreateImage", result));
        }

        VkImageMemoryRequirementsInfo2 requirements_info{VK_STRUCTURE_TYPE_IMAGE_MEMORY_REQUIREMENTS_INFO_2};
        requirements_info.image = shared.image;
        VkMemoryRequirements2 requirements{VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2};
        vk.get_image_requirements(info.device, &requirements_info, &requirements);
        uint32_t allowed = requirements.memoryRequirements.memoryTypeBits;
        VkMemoryWin32HandlePropertiesKHR handle_props{VK_STRUCTURE_TYPE_MEMORY_WIN32_HANDLE_PROPERTIES_KHR};
        if (vk.get_handle_properties(info.device, VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D12_RESOURCE_BIT, handle,
                                     &handle_props) == VK_SUCCESS &&
            (allowed & handle_props.memoryTypeBits)) {
            allowed &= handle_props.memoryTypeBits;
        }
        uint32_t type_index{};
        if (!PickMemoryType(allowed, type_index)) {
            return Fail(error, "no memory type can hold the shared image");
        }

        // The dedicated allocation is mandatory for a D3D12 resource handle.
        VkMemoryDedicatedAllocateInfo dedicated{VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO};
        dedicated.image = shared.image;
        VkImportMemoryWin32HandleInfoKHR import{VK_STRUCTURE_TYPE_IMPORT_MEMORY_WIN32_HANDLE_INFO_KHR};
        import.pNext = &dedicated;
        import.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D12_RESOURCE_BIT;
        import.handle = handle;
        VkMemoryAllocateInfo allocate{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
        allocate.pNext = &import;
        allocate.allocationSize = requirements.memoryRequirements.size;
        allocate.memoryTypeIndex = type_index;
        result = vk.allocate_memory(info.device, &allocate, nullptr, &shared.memory);
        if (result != VK_SUCCESS) {
            return Fail(error, Vk("vkAllocateMemory (import of the D3D12 resource)", result));
        }
        result = vk.bind_image_memory(info.device, shared.image, shared.memory, 0);
        if (result != VK_SUCCESS) {
            return Fail(error, Vk("vkBindImageMemory", result));
        }
        return true;
    }

    void DestroyShared(SharedImage& shared) {
        if (shared.image) {
            vk.destroy_image(info.device, shared.image, nullptr);
        }
        if (shared.memory) {
            vk.free_memory(info.device, shared.memory, nullptr);
        }
        shared.resource.Reset();
        shared.image = nullptr;
        shared.memory = nullptr;
        shared.layout = VK_IMAGE_LAYOUT_UNDEFINED;
        shared.format = nullptr;
    }

    bool CreateDepthBuffer(std::string* error) {
        VkBufferCreateInfo create{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
        create.size = VkDeviceSize{max_w} * max_h * 4;
        create.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        create.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        VkResult result = vk.create_buffer(info.device, &create, nullptr, &depth_buffer);
        if (result != VK_SUCCESS) {
            return Fail(error, Vk("vkCreateBuffer (depth staging)", result));
        }
        VkMemoryRequirements requirements;
        vk.get_buffer_requirements(info.device, depth_buffer, &requirements);
        uint32_t type_index{};
        if (!PickMemoryType(requirements.memoryTypeBits, type_index)) {
            return Fail(error, "no memory type for the depth staging buffer");
        }
        VkMemoryAllocateInfo allocate{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
        allocate.allocationSize = requirements.size;
        allocate.memoryTypeIndex = type_index;
        result = vk.allocate_memory(info.device, &allocate, nullptr, &depth_memory);
        if (result != VK_SUCCESS) {
            return Fail(error, Vk("vkAllocateMemory (depth staging)", result));
        }
        result = vk.bind_buffer_memory(info.device, depth_buffer, depth_memory, 0);
        if (result != VK_SUCCESS) {
            return Fail(error, Vk("vkBindBufferMemory (depth staging)", result));
        }
        return true;
    }

    /// The FFX context and every image, buffer: the caller already made the GPU idle.
    void DestroyResources() {
        if (context) {
            ffx_destroy(&context, nullptr);
            context = nullptr;
        }
        DestroyShared(color);
        DestroyShared(depth);
        DestroyShared(motion);
        DestroyShared(output);
        if (depth_buffer) {
            vk.destroy_buffer(info.device, depth_buffer, nullptr);
            depth_buffer = nullptr;
        }
        if (depth_memory) {
            vk.free_memory(info.device, depth_memory, nullptr);
            depth_memory = nullptr;
        }
    }

    bool Configure(uint32_t max_render_w, uint32_t max_render_h, uint32_t output_w, uint32_t output_h,
                   VkFormat color_format, VkFormat depth_format, VkFormat motion_format,
                   uint32_t create_flags, std::string* error) {
        if (!device) {
            return Fail(error, "the bridge is not initialised");
        }
        if (!CheckNotBroken(error)) {
            return false;
        }
        if (!max_render_w || !max_render_h || !output_w || !output_h) {
            return Fail(error, "sizes must not be zero");
        }
        // Everything is checked before the current configuration is touched.
        const FormatInfo* color_info = FindFormat(color_format);
        if (!color_info) {
            return Fail(error, "unsupported colour format " + std::to_string(static_cast<int>(color_format)));
        }
        if (depth_format != VK_FORMAT_D32_SFLOAT && depth_format != VK_FORMAT_D32_SFLOAT_S8_UINT) {
            return Fail(error, "unsupported depth format " + std::to_string(static_cast<int>(depth_format)) +
                                   " (D32_SFLOAT or D32_SFLOAT_S8_UINT only)");
        }
        if (motion_format != VK_FORMAT_R16G16_SFLOAT) {
            return Fail(error, "unsupported motion vector format " +
                                   std::to_string(static_cast<int>(motion_format)) +
                                   " (R16G16_SFLOAT only)");
        }
        // A failed wait means D3D12 may still use the old images: keep them and fail.
        if (!WaitFence(last_dispatch, error)) {
            return false;
        }
        DestroyResources();
        max_w = max_render_w;
        max_h = max_render_h;
        out_w = output_w;
        out_h = output_h;
        const VkImageUsageFlags input_usage =
            VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
        const VkImageUsageFlags output_usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
                                               VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_STORAGE_BIT;
        const bool ok =
            CreateShared(color, color_info->game, max_w, max_h, input_usage, false, error) &&
            CreateShared(depth, VK_FORMAT_R32_SFLOAT, max_w, max_h, input_usage, false, error) &&
            CreateShared(motion, VK_FORMAT_R16G16_SFLOAT, max_w, max_h, input_usage, false, error) &&
            CreateShared(output, VK_FORMAT_R16G16B16A16_SFLOAT, out_w, out_h, output_usage, true, error) &&
            CreateDepthBuffer(error) && CreateContext(create_flags, error);
        if (!ok) {
            DestroyResources();
            return false;
        }
        std::printf("HelixSR: configured %ux%u max render -> %ux%u, colour %s\n", max_w, max_h, out_w,
                    out_h, color.format->name);
        return true;
    }

    bool CreateContext(uint32_t flags, std::string* error) {
        ffxCreateBackendDX12Desc backend{};
        backend.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_BACKEND_DX12;
        backend.device = device.Get();
        ffxCreateContextDescUpscale create{};
        create.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE;
        create.header.pNext = &backend.header;
        create.flags = flags;
        create.maxRenderSize = {max_w, max_h};
        create.maxUpscaleSize = {out_w, out_h};
        create.fpMessage = FfxMessage;
        if (const uint32_t result = ffx_create(&context, &create.header, nullptr); result != FFX_API_RETURN_OK) {
            context = nullptr;
            return Fail(error, std::string{"ffxCreateContext failed: "} + FfxResultName(result) + " (" +
                                   std::to_string(result) + ")");
        }
        return true;
    }

    uint64_t RecordInputs(VkCommandBuffer cmd, VkImage game_color, VkImage game_depth,
                          VkImage game_motion, uint32_t render_w, uint32_t render_h) {
        if (broken || !context || !render_w || !render_h || render_w > max_w || render_h > max_h) {
            return 0;
        }
        const uint32_t family = info.queue_family;
        SharedImage* const inputs[] = {&color, &depth, &motion};

        // Game images become readable for the copies; the shared inputs come over from D3D12.
        VkMemoryBarrier memory{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        memory.srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT;
        memory.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT;
        std::array<VkImageMemoryBarrier, 3> acquire;
        for (size_t i = 0; i < acquire.size(); ++i) {
            acquire[i] = ImageBarrier(inputs[i]->image, VK_IMAGE_LAYOUT_UNDEFINED,
                                      VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0,
                                      VK_ACCESS_TRANSFER_WRITE_BIT, VK_QUEUE_FAMILY_EXTERNAL, family);
        }
        vk.cmd_pipeline_barrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0,
                                1, &memory, 0, nullptr, static_cast<uint32_t>(acquire.size()),
                                acquire.data());

        VkImageCopy copy{};
        copy.srcSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        copy.dstSubresource = copy.srcSubresource;
        copy.extent = {render_w, render_h, 1};
        vk.cmd_copy_image(cmd, game_color, VK_IMAGE_LAYOUT_GENERAL, color.image,
                          VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
        vk.cmd_copy_image(cmd, game_motion, VK_IMAGE_LAYOUT_GENERAL, motion.image,
                          VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);

        // A depth image cannot be copied into a colour image: go through a buffer.
        VkBufferImageCopy to_buffer{};
        to_buffer.bufferRowLength = render_w;
        to_buffer.bufferImageHeight = render_h;
        to_buffer.imageSubresource = {VK_IMAGE_ASPECT_DEPTH_BIT, 0, 0, 1};
        to_buffer.imageExtent = {render_w, render_h, 1};
        vk.cmd_copy_image_to_buffer(cmd, game_depth, VK_IMAGE_LAYOUT_GENERAL, depth_buffer, 1, &to_buffer);
        VkMemoryBarrier buffer_ready{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        buffer_ready.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        buffer_ready.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        vk.cmd_pipeline_barrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1,
                                &buffer_ready, 0, nullptr, 0, nullptr);
        VkBufferImageCopy from_buffer = to_buffer;
        from_buffer.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
        vk.cmd_copy_buffer_to_image(cmd, depth_buffer, depth.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1,
                                    &from_buffer);

        // Release to D3D12; the execution dependency lets the game write its images again.
        std::array<VkImageMemoryBarrier, 4> release;
        uint32_t release_count = 0;
        for (SharedImage* input : inputs) {
            release[release_count++] =
                ImageBarrier(input->image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL,
                             VK_ACCESS_TRANSFER_WRITE_BIT, 0, family, VK_QUEUE_FAMILY_EXTERNAL);
            input->layout = VK_IMAGE_LAYOUT_GENERAL;
        }
        // The first frame after Configure: give the output its layout now, before D3D12 writes it.
        // RecordOutput then acquires from GENERAL, and no UNDEFINED transition can discard the result.
        if (output.layout == VK_IMAGE_LAYOUT_UNDEFINED) {
            release[release_count++] =
                ImageBarrier(output.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0, 0, family,
                             VK_QUEUE_FAMILY_EXTERNAL);
            output.layout = VK_IMAGE_LAYOUT_GENERAL;
        }
        vk.cmd_pipeline_barrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0,
                                0, nullptr, 0, nullptr, release_count, release.data());
        return ++counter;
    }

    FfxApiResource Describe(const SharedImage& shared, uint32_t state, uint32_t usage) const {
        FfxApiResource resource{};
        resource.resource = shared.resource.Get();
        resource.description.type = FFX_API_RESOURCE_TYPE_TEXTURE2D;
        resource.description.format = shared.format->ffx;
        resource.description.width = shared.width;
        resource.description.height = shared.height;
        resource.description.depth = 1;
        resource.description.mipCount = 1;
        resource.description.usage = usage;
        resource.state = state;
        return resource;
    }

    uint64_t Dispatch(uint64_t inputs_ready, const FrameParams& params, std::string* error) {
        if (!CheckNotBroken(error)) {
            return 0;
        }
        if (!context) {
            Fail(error, "the bridge is not configured");
            return 0;
        }
        if (!params.render_w || !params.render_h || params.render_w > max_w || params.render_h > max_h) {
            Fail(error, "render size is outside the configured maximum");
            return 0;
        }
        if (!inputs_ready || inputs_ready > counter) {
            Fail(error, "inputs_ready is not a value RecordInputs returned");
            return 0;
        }
        const uint32_t slot = static_cast<uint32_t>(frame % AllocatorCount);
        if (!WaitFence(slot_value[slot], error)) {
            return 0;
        }
        HRESULT hr = allocators[slot]->Reset();
        if (SUCCEEDED(hr)) {
            hr = list->Reset(allocators[slot].Get(), nullptr);
        }
        if (FAILED(hr)) {
            Fail(error, Hr("resetting the D3D12 command list", hr));
            return 0;
        }

        constexpr auto Common = D3D12_RESOURCE_STATE_COMMON;
        constexpr auto Read = D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
        constexpr auto Write = D3D12_RESOURCE_STATE_UNORDERED_ACCESS;
        std::array<D3D12_RESOURCE_BARRIER, 4> barriers;
        Transition(barriers[0], color.resource.Get(), Common, Read);
        Transition(barriers[1], depth.resource.Get(), Common, Read);
        Transition(barriers[2], motion.resource.Get(), Common, Read);
        Transition(barriers[3], output.resource.Get(), Common, Write);
        list->ResourceBarrier(static_cast<UINT>(barriers.size()), barriers.data());

        ffxDispatchDescUpscale dispatch{};
        dispatch.header.type = FFX_API_DISPATCH_DESC_TYPE_UPSCALE;
        dispatch.commandList = list.Get();
        dispatch.color = Describe(color, FFX_API_RESOURCE_STATE_COMPUTE_READ, 0);
        dispatch.depth = Describe(depth, FFX_API_RESOURCE_STATE_COMPUTE_READ, 0);
        dispatch.motionVectors = Describe(motion, FFX_API_RESOURCE_STATE_COMPUTE_READ, 0);
        dispatch.output = Describe(output, FFX_API_RESOURCE_STATE_UNORDERED_ACCESS,
                                   FFX_API_RESOURCE_USAGE_UAV);
        dispatch.jitterOffset = {params.jitter_x, params.jitter_y};
        dispatch.motionVectorScale = {params.motion_scale_x, params.motion_scale_y};
        dispatch.renderSize = {params.render_w, params.render_h};
        dispatch.upscaleSize = {out_w, out_h};
        dispatch.enableSharpening = params.sharpen;
        dispatch.sharpness = params.sharpness;
        dispatch.frameTimeDelta = params.frame_ms;
        dispatch.preExposure = params.pre_exposure;
        dispatch.reset = params.reset;
        dispatch.cameraNear = params.near_plane;
        dispatch.cameraFar = params.far_plane;
        dispatch.cameraFovAngleVertical = params.fov_y;
        dispatch.viewSpaceToMetersFactor = 1.0f;
        dispatch.flags = 0;
        if (const uint32_t result = ffx_dispatch(&context, &dispatch.header); result != FFX_API_RETURN_OK) {
            list->Close();
            Fail(error, std::string{"ffxDispatch failed: "} + FfxResultName(result) + " (" +
                            std::to_string(result) + ")");
            return 0;
        }

        Transition(barriers[0], color.resource.Get(), Read, Common);
        Transition(barriers[1], depth.resource.Get(), Read, Common);
        Transition(barriers[2], motion.resource.Get(), Read, Common);
        Transition(barriers[3], output.resource.Get(), Write, Common);
        list->ResourceBarrier(static_cast<UINT>(barriers.size()), barriers.data());
        hr = list->Close();
        if (FAILED(hr)) {
            Fail(error, Hr("closing the D3D12 command list", hr));
            return 0;
        }

        ID3D12CommandList* lists[] = {list.Get()};
        hr = queue->Wait(fence.Get(), inputs_ready);
        if (FAILED(hr)) {
            Fault(error, Hr("ID3D12CommandQueue::Wait", hr));
            return 0;
        }
        queue->ExecuteCommandLists(1, lists);
        const uint64_t signal = ++counter;
        // The work is in flight from here on: the allocator slot and Destroy() must wait for it even
        // if the Signal below fails.
        slot_value[slot] = signal;
        last_dispatch = signal;
        ++frame;
        hr = queue->Signal(fence.Get(), signal);
        if (FAILED(hr)) {
            Fault(error, Hr("ID3D12CommandQueue::Signal", hr));
            return 0;
        }
        hr = device->GetDeviceRemovedReason();
        if (hr != S_OK) {
            Fault(error, Hr("the D3D12 device was removed", hr));
            return 0;
        }
        if (!logged_dispatch) {
            logged_dispatch = true;
            std::printf("HelixSR: first dispatch submitted (%ux%u -> %ux%u)\n", params.render_w,
                        params.render_h, out_w, out_h);
        }
        return signal;
    }

    void RecordOutput(VkCommandBuffer cmd, VkImage game_output) {
        // RecordInputs gave the output its GENERAL layout before D3D12 first wrote it; without that
        // call there is no result to read.
        if (broken || !context || output.layout != VK_IMAGE_LAYOUT_GENERAL) {
            return;
        }
        const uint32_t family = info.queue_family;
        VkMemoryBarrier memory{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        memory.srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT;
        memory.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        // The contents are D3D12's result: keep them across the ownership transfer.
        VkImageMemoryBarrier acquire =
            ImageBarrier(output.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, 0,
                         VK_ACCESS_TRANSFER_READ_BIT, VK_QUEUE_FAMILY_EXTERNAL, family);
        vk.cmd_pipeline_barrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0,
                                1, &memory, 0, nullptr, 1, &acquire);

        VkImageCopy copy{};
        copy.srcSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        copy.dstSubresource = copy.srcSubresource;
        copy.extent = {out_w, out_h, 1};
        vk.cmd_copy_image(cmd, output.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, game_output,
                          VK_IMAGE_LAYOUT_GENERAL, 1, &copy);

        VkMemoryBarrier done{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        done.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        done.dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
        VkImageMemoryBarrier release =
            ImageBarrier(output.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL, 0, 0,
                         family, VK_QUEUE_FAMILY_EXTERNAL);
        vk.cmd_pipeline_barrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0,
                                1, &done, 0, nullptr, 1, &release);
    }

    void ResetState() {
        counter = last_dispatch = frame = 0;
        slot_value.fill(0);
        logged_dispatch = false;
        broken = false;
        broken_reason.clear();
    }

    /// The GPU did not finish (timeout, failed wait, removed device, untrusted fence). The context,
    /// the D3D12 objects and the shared resources may still be executing or referenced, so they are
    /// leaked on purpose. The Vulkan images, memory, buffer and semaphore wrap those same resources
    /// and the fence, and a Vulkan submit waiting on that fence may still be pending, so the caller's
    /// idle guarantee cannot be proven for them either: they are leaked too. A leak costs memory;
    /// releasing something the GPU still uses is a use-after-free.
    void Abandon() {
        std::printf("HelixSR: the GPU did not finish; leaking the FFX context, the D3D12 objects and the "
                    "shared Vulkan objects\n");
        context = nullptr;
        for (SharedImage* shared : {&color, &depth, &motion, &output}) {
            shared->resource.Leak();
            shared->image = nullptr;
            shared->memory = nullptr;
            shared->layout = VK_IMAGE_LAYOUT_UNDEFINED;
            shared->format = nullptr;
        }
        depth_buffer = nullptr;
        depth_memory = nullptr;
        list.Leak();
        for (auto& allocator : allocators) {
            allocator.Leak();
        }
        queue.Leak();
        fence.Leak();
        device.Leak();
        factory.Reset();
        event = nullptr;
        semaphore = nullptr;
        ResetState();
    }

    void Destroy() {
        std::string wait_error;
        if (broken || !WaitFence(last_dispatch, &wait_error)) {
            if (device) {
                Abandon();
            }
            return;
        }
        DestroyResources();
        list.Reset();
        for (auto& allocator : allocators) {
            allocator.Reset();
        }
        queue.Reset();
        fence.Reset();
        device.Reset();
        factory.Reset();
        if (event) {
            CloseHandle(event);
            event = nullptr;
        }
        if (semaphore) {
            vk.destroy_semaphore(info.device, semaphore, nullptr);
            semaphore = nullptr;
        }
        ResetState();
    }
};

Bridge::Bridge() : impl{std::make_unique<Impl>()} {}
Bridge::~Bridge() = default;

bool Bridge::Init(const DeviceInfo& info, const wchar_t* dll_path, std::string* error) {
    const bool ok = impl->Init(info, dll_path, error);
    if (!ok) {
        impl->Destroy();
    } else {
        std::printf("HelixSR: bridge initialised\n");
    }
    return ok;
}

bool Bridge::Configure(uint32_t max_render_w, uint32_t max_render_h, uint32_t out_w, uint32_t out_h,
                       VkFormat color_format, VkFormat depth_format, VkFormat motion_format,
                       uint32_t ffx_create_flags, std::string* error) {
    return impl->Configure(max_render_w, max_render_h, out_w, out_h, color_format, depth_format,
                           motion_format, ffx_create_flags, error);
}

uint64_t Bridge::RecordInputs(VkCommandBuffer cmd, VkImage color, VkImage depth, VkImage motion,
                              uint32_t render_w, uint32_t render_h) {
    return impl->RecordInputs(cmd, color, depth, motion, render_w, render_h);
}

uint64_t Bridge::Dispatch(uint64_t inputs_ready, const FrameParams& params, std::string* error) {
    return impl->Dispatch(inputs_ready, params, error);
}

void Bridge::RecordOutput(VkCommandBuffer cmd, VkImage output) {
    impl->RecordOutput(cmd, output);
}

VkSemaphore Bridge::Semaphore() const {
    return impl->semaphore;
}

void Bridge::Destroy() {
    impl->Destroy();
}

} // namespace Vulkan::HelixSr

#else // !_WIN32

namespace Vulkan::HelixSr {

struct Bridge::Impl {};

Bridge::Bridge() = default;
Bridge::~Bridge() = default;

bool Bridge::Init(const DeviceInfo&, const wchar_t*, std::string* error) {
    if (error) {
        *error = "the HelixSR bridge needs Windows (Direct3D 12)";
    }
    return false;
}

bool Bridge::Configure(uint32_t, uint32_t, uint32_t, uint32_t, VkFormat, VkFormat, VkFormat, uint32_t,
                       std::string* error) {
    if (error) {
        *error = "the HelixSR bridge needs Windows (Direct3D 12)";
    }
    return false;
}

uint64_t Bridge::RecordInputs(VkCommandBuffer, VkImage, VkImage, VkImage, uint32_t, uint32_t) {
    return 0;
}

uint64_t Bridge::Dispatch(uint64_t, const FrameParams&, std::string* error) {
    if (error) {
        *error = "the HelixSR bridge needs Windows (Direct3D 12)";
    }
    return 0;
}

void Bridge::RecordOutput(VkCommandBuffer, VkImage) {}

VkSemaphore Bridge::Semaphore() const {
    return VK_NULL_HANDLE;
}

void Bridge::Destroy() {}

} // namespace Vulkan::HelixSr

#endif
