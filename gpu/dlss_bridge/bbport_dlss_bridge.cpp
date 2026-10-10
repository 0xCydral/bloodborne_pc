// SPDX-FileCopyrightText: Copyright 2026 IFreemz (shadps4_dlss bridge), bbport contributors
// SPDX-License-Identifier: MIT
//
// bbport_dlss.dll: the only part of the project that uses the NVIDIA DLSS (NGX) SDK. Built with
// MSVC (the SDK's libraries are MSVC only); the port loads it at run time.
// Adapted from IFreemz/shadPS4-Bloodborne-DLSS-FSR (dlss_bridge).

#include <algorithm>
#include <array>
#include <cfloat>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include "bbport_dlss_bridge.h" // Vulkan first: the NGX headers expect its types

#include <nvsdk_ngx_helpers.h>
#include <nvsdk_ngx_vk.h>
#include <nvsdk_ngx_helpers_vk.h>
#include <nvsdk_ngx_helpers_dlssg_vk.h>

namespace {

constexpr char ProjectId[] = "42359704-c9f3-4806-9fb5-d469000fdb8a";
constexpr char EngineVersion[] = "bbport-windows";

struct State {
    // One lock for every NGX call: Super Resolution is evaluated on the recording threads, Frame
    // Generation on the GPU command thread, and NGX's own state is shared between them.
    std::mutex mutex;
    BbDlssLogFn log{};
    std::wstring dll_directory;
    std::wstring data_directory;
    const wchar_t* dll_path{};
    NVSDK_NGX_FeatureCommonInfo common{};
    NVSDK_NGX_FeatureDiscoveryInfo discovery{};    // Super Resolution
    NVSDK_NGX_FeatureDiscoveryInfo fg_discovery{}; // Frame Generation
    NVSDK_NGX_Parameter* capabilities{};
    NVSDK_NGX_Parameter* parameters{};
    NVSDK_NGX_Handle* feature{};
    BbDlssFeature feature_desc{};
    NVSDK_NGX_Parameter* fg_parameters{}; // a map of its own: nothing set for one leaks to the other
    NVSDK_NGX_Handle* fg_feature{};
    BbDlssFrameGenFeature fg_desc{};
    std::vector<VkExtensionProperties> instance_extensions, device_extensions;
    VkDevice device{};
    bool configured{};
    bool initialized{};
} state;

template <typename... Args>
void Log(int warning, const char* format, Args... args) {
    if (!state.log)
        return;
    std::array<char, 1024> text{};
    std::snprintf(text.data(), text.size(), format, args...);
    state.log(warning, text.data());
}

bool Check(const char* operation, NVSDK_NGX_Result result) {
    if (NVSDK_NGX_FAILED(result)) {
        Log(1, "%s failed: 0x%08x", operation, static_cast<unsigned>(result));
        return false;
    }
    return true;
}

void NVSDK_CONV NgxLog(const char* message, NVSDK_NGX_Logging_Level, NVSDK_NGX_Feature) {
    Log(0, "NGX: %s", message ? message : "");
}

// The union of what Super Resolution and Frame Generation need, by name, with the higher version.
void AppendUnique(std::vector<VkExtensionProperties>& out, const VkExtensionProperties* in,
                  uint32_t count) {
    for (uint32_t i = 0; i < count; ++i) {
        const auto same = std::find_if(out.begin(), out.end(), [&](const VkExtensionProperties& e) {
            return std::strcmp(e.extensionName, in[i].extensionName) == 0;
        });
        if (same == out.end()) {
            out.push_back(in[i]);
        } else {
            same->specVersion = std::max(same->specVersion, in[i].specVersion);
        }
    }
}

int32_t Configure(const wchar_t* dll_directory, const wchar_t* data_directory, BbDlssLogFn log) {
    std::scoped_lock lock{state.mutex};
    state.log = log;
    state.dll_directory = dll_directory ? dll_directory : L".";
    state.data_directory = data_directory ? data_directory : L".";
    state.dll_path = state.dll_directory.c_str();
    state.common.PathListInfo = {&state.dll_path, 1};
    // BB_NGX_LOG=1 (or 2, verbose) sends NGX's own log lines to the game log.
    const char* log_level = std::getenv("BB_NGX_LOG");
    const NVSDK_NGX_Logging_Level level = !log_level || log_level[0] == '0'
                                              ? NVSDK_NGX_LOGGING_LEVEL_OFF
                                          : log_level[0] == '2' ? NVSDK_NGX_LOGGING_LEVEL_VERBOSE
                                                                : NVSDK_NGX_LOGGING_LEVEL_ON;
    state.common.LoggingInfo = {NgxLog, level, false};
    state.discovery.SDKVersion = NVSDK_NGX_Version_API;
    state.discovery.FeatureID = NVSDK_NGX_Feature_SuperSampling;
    state.discovery.Identifier.IdentifierType = NVSDK_NGX_Application_Identifier_Type_Project_Id;
    state.discovery.Identifier.v.ProjectDesc = {ProjectId, NVSDK_NGX_ENGINE_TYPE_CUSTOM,
                                                EngineVersion};
    state.discovery.ApplicationDataPath = state.data_directory.c_str();
    state.discovery.FeatureInfo = &state.common;
    state.fg_discovery = state.discovery;
    state.fg_discovery.FeatureID = NVSDK_NGX_Feature_FrameGeneration;
    state.configured = true;
    return 1;
}

int32_t InstanceExtensions(uint32_t* count, const VkExtensionProperties** extensions) {
    std::scoped_lock lock{state.mutex};
    VkExtensionProperties* required{};
    uint32_t required_count{};
    if (!state.configured ||
        !Check("Instance extension query",
               NVSDK_NGX_VULKAN_GetFeatureInstanceExtensionRequirements(
                   &state.discovery, &required_count, &required)))
        return 0;
    state.instance_extensions.clear();
    AppendUnique(state.instance_extensions, required, required_count);
    // Frame Generation's are optional: a system without it keeps Super Resolution.
    VkExtensionProperties* fg_required{};
    uint32_t fg_count{};
    if (NVSDK_NGX_SUCCEED(NVSDK_NGX_VULKAN_GetFeatureInstanceExtensionRequirements(
            &state.fg_discovery, &fg_count, &fg_required)))
        AppendUnique(state.instance_extensions, fg_required, fg_count);
    *count = static_cast<uint32_t>(state.instance_extensions.size());
    *extensions = state.instance_extensions.data();
    return 1;
}

int32_t DeviceExtensions(VkInstance instance, VkPhysicalDevice physical, uint32_t* count,
                         const VkExtensionProperties** extensions) {
    std::scoped_lock lock{state.mutex};
    if (!state.configured)
        return 0;
    NVSDK_NGX_FeatureRequirement support{};
    if (!Check("Feature requirement query",
               NVSDK_NGX_VULKAN_GetFeatureRequirements(instance, physical, &state.discovery,
                                                       &support)))
        return 0;
    if (support.FeatureSupported != NVSDK_NGX_FeatureSupportResult_Supported) {
        Log(1, "DLSS is not supported on this GPU or driver (code 0x%x)",
            static_cast<unsigned>(support.FeatureSupported));
        return 0;
    }
    VkExtensionProperties* required{};
    uint32_t required_count{};
    if (!Check("Device extension query",
               NVSDK_NGX_VULKAN_GetFeatureDeviceExtensionRequirements(
                   instance, physical, &state.discovery, &required_count, &required)))
        return 0;
    state.device_extensions.clear();
    AppendUnique(state.device_extensions, required, required_count);
    // Frame Generation (GeForce RTX 40 and newer): its extensions only where it is supported.
    NVSDK_NGX_FeatureRequirement fg_support{};
    if (NVSDK_NGX_SUCCEED(NVSDK_NGX_VULKAN_GetFeatureRequirements(instance, physical,
                                                                  &state.fg_discovery,
                                                                  &fg_support)) &&
        fg_support.FeatureSupported == NVSDK_NGX_FeatureSupportResult_Supported) {
        VkExtensionProperties* fg_required{};
        uint32_t fg_count{};
        if (Check("Frame Generation device extension query",
                  NVSDK_NGX_VULKAN_GetFeatureDeviceExtensionRequirements(
                      instance, physical, &state.fg_discovery, &fg_count, &fg_required)))
            AppendUnique(state.device_extensions, fg_required, fg_count);
    } else {
        Log(0, "DLSS Frame Generation is not supported on this GPU or driver (code 0x%x)",
            static_cast<unsigned>(fg_support.FeatureSupported));
    }
    *count = static_cast<uint32_t>(state.device_extensions.size());
    *extensions = state.device_extensions.data();
    return 1;
}

int32_t Initialize(VkInstance instance, VkPhysicalDevice physical, VkDevice device,
                   PFN_vkGetInstanceProcAddr get_instance_proc,
                   PFN_vkGetDeviceProcAddr get_device_proc) {
    std::scoped_lock lock{state.mutex};
    if (!state.configured || state.initialized)
        return 0;
    if (!Check("NGX initialization",
               NVSDK_NGX_VULKAN_Init_with_ProjectID(
                   ProjectId, NVSDK_NGX_ENGINE_TYPE_CUSTOM, EngineVersion,
                   state.data_directory.c_str(), instance, physical, device, get_instance_proc,
                   get_device_proc, &state.common)))
        return 0;
    state.initialized = true;
    state.device = device;
    if (!Check("Capability query", NVSDK_NGX_VULKAN_GetCapabilityParameters(&state.capabilities)))
        return 0;
    int available{}, needs_driver{};
    NVSDK_NGX_Parameter_GetI(state.capabilities, NVSDK_NGX_Parameter_SuperSampling_Available,
                             &available);
    NVSDK_NGX_Parameter_GetI(state.capabilities,
                             NVSDK_NGX_Parameter_SuperSampling_NeedsUpdatedDriver, &needs_driver);
    if (needs_driver) {
        Log(1, "DLSS needs a newer NVIDIA driver");
        return 0;
    }
    if (!available) {
        Log(1, "DLSS Super Resolution is not available on this system");
        return 0;
    }
    return 1;
}

void ReleaseFeatureLocked() {
    if (state.feature) {
        NVSDK_NGX_VULKAN_ReleaseFeature(state.feature);
        state.feature = nullptr;
    }
    if (state.parameters) {
        NVSDK_NGX_VULKAN_DestroyParameters(state.parameters);
        state.parameters = nullptr;
    }
}

void ReleaseFeature() {
    std::scoped_lock lock{state.mutex};
    ReleaseFeatureLocked();
}

int32_t CreateFeature(VkCommandBuffer command, const BbDlssFeature* desc) {
    std::scoped_lock lock{state.mutex};
    if (!state.initialized || !state.capabilities || !desc)
        return 0;
    ReleaseFeatureLocked();
    if (!Check("Parameter allocation", NVSDK_NGX_VULKAN_AllocateParameters(&state.parameters)))
        return 0;
    for (const char* key : {NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_DLAA,
                            NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Quality,
                            NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Balanced,
                            NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Performance,
                            NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_UltraPerformance,
                            NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_UltraQuality})
        NVSDK_NGX_Parameter_SetUI(state.parameters, key, desc->preset);
    static constexpr std::array qualities{
        NVSDK_NGX_PerfQuality_Value_DLAA, NVSDK_NGX_PerfQuality_Value_MaxQuality,
        NVSDK_NGX_PerfQuality_Value_Balanced, NVSDK_NGX_PerfQuality_Value_MaxPerf,
        NVSDK_NGX_PerfQuality_Value_UltraPerformance};
    NVSDK_NGX_DLSS_Create_Params settings{};
    settings.Feature.InWidth = desc->input_width;
    settings.Feature.InHeight = desc->input_height;
    settings.Feature.InTargetWidth = desc->output_width;
    settings.Feature.InTargetHeight = desc->output_height;
    settings.Feature.InPerfQualityValue = qualities[std::clamp(desc->quality, 0, 4)];
    settings.InFeatureCreateFlags =
        NVSDK_NGX_DLSS_Feature_Flags_MVLowRes |
        (desc->depth_inverted ? NVSDK_NGX_DLSS_Feature_Flags_DepthInverted : 0) |
        (desc->hdr ? NVSDK_NGX_DLSS_Feature_Flags_IsHDR | NVSDK_NGX_DLSS_Feature_Flags_AutoExposure
                   : 0);
    const auto result = NGX_VULKAN_CREATE_DLSS_EXT1(state.device, command, 1, 1, &state.feature,
                                                   state.parameters, &settings);
    if (!Check("DLSS feature creation", result) || !state.feature) {
        state.feature = nullptr;
        return 0;
    }
    state.feature_desc = *desc;
    Log(0, "DLSS %ux%u -> %ux%u, quality %d, preset %u%s", desc->input_width, desc->input_height,
        desc->output_width, desc->output_height, desc->quality, desc->preset,
        desc->hdr ? ", HDR input" : "");
    return 1;
}

NVSDK_NGX_Resource_VK Wrap(const BbDlssImage& image, bool writable) {
    return NVSDK_NGX_Create_ImageView_Resource_VK(image.view, image.image, image.range,
                                                  image.format, image.width, image.height,
                                                  writable);
}

int32_t Evaluate(VkCommandBuffer command, const BbDlssEvaluate* evaluate) {
    std::scoped_lock lock{state.mutex};
    if (!state.feature || !state.parameters || !evaluate)
        return 0;
    auto color = Wrap(evaluate->color, false);
    auto depth = Wrap(evaluate->depth, false);
    auto motion = Wrap(evaluate->motion, false);
    auto output = Wrap(evaluate->output, true);
    NVSDK_NGX_VK_DLSS_Eval_Params parameters{};
    parameters.Feature.pInColor = &color;
    parameters.Feature.pInOutput = &output;
    parameters.Feature.InSharpness = evaluate->sharpness;
    parameters.pInDepth = &depth;
    parameters.pInMotionVectors = &motion;
    parameters.InRenderSubrectDimensions = {state.feature_desc.input_width,
                                            state.feature_desc.input_height};
    parameters.InJitterOffsetX = evaluate->jitter_x;
    parameters.InJitterOffsetY = evaluate->jitter_y;
    parameters.InReset = evaluate->reset;
    parameters.InMVScaleX = parameters.InMVScaleY = 1.0f;
    parameters.InPreExposure = parameters.InExposureScale = 1.0f;
    parameters.InFrameTimeDeltaInMsec = evaluate->frame_ms;
    return Check("DLSS evaluation", NGX_VULKAN_EVALUATE_DLSS_EXT(command, state.feature,
                                                                state.parameters, &parameters))
               ? 1
               : 0;
}

// ---- Frame Generation (abi 2) --------------------------------------------------------------------

int32_t FrameGenAvailable(int32_t* available, int32_t* init_result) {
    std::scoped_lock lock{state.mutex};
    if (!state.initialized || !state.capabilities)
        return 0;
    int supported{}, result{}, needs_driver{};
    NVSDK_NGX_Parameter_GetI(state.capabilities, NVSDK_NGX_Parameter_FrameGeneration_Available,
                             &supported);
    NVSDK_NGX_Parameter_GetI(state.capabilities, NVSDK_NGX_Parameter_FrameGeneration_FeatureInitResult,
                             &result);
    NVSDK_NGX_Parameter_GetI(state.capabilities,
                             NVSDK_NGX_Parameter_FrameGeneration_NeedsUpdatedDriver, &needs_driver);
    if (needs_driver) {
        Log(1, "DLSS Frame Generation needs a newer NVIDIA driver");
        supported = 0;
    }
    if (available)
        *available = supported ? 1 : 0;
    if (init_result)
        *init_result = result;
    return 1;
}

void ReleaseFrameGenLocked() {
    if (state.fg_feature) {
        NVSDK_NGX_VULKAN_ReleaseFeature(state.fg_feature);
        state.fg_feature = nullptr;
    }
    if (state.fg_parameters) {
        NVSDK_NGX_VULKAN_DestroyParameters(state.fg_parameters);
        state.fg_parameters = nullptr;
    }
}

void ReleaseFrameGen() {
    std::scoped_lock lock{state.mutex};
    ReleaseFrameGenLocked();
}

int32_t CreateFrameGen(VkCommandBuffer command, const BbDlssFrameGenFeature* desc) {
    std::scoped_lock lock{state.mutex};
    if (!state.initialized || !state.capabilities || !desc)
        return 0;
    ReleaseFrameGenLocked();
    if (!Check("Frame Generation parameter allocation",
               NVSDK_NGX_VULKAN_AllocateParameters(&state.fg_parameters)))
        return 0;
    NVSDK_NGX_DLSSG_Create_Params settings{};
    settings.Width = desc->width;
    settings.Height = desc->height;
    settings.NativeBackbufferFormat = static_cast<unsigned int>(desc->format);
    settings.RenderWidth = desc->render_width;
    settings.RenderHeight = desc->render_height;
    settings.DynamicResolutionScaling = false;
    const auto result = NGX_VK_CREATE_DLSSG(command, 1, 1, &state.fg_feature, state.fg_parameters,
                                            &settings);
    if (!Check("DLSS Frame Generation creation", result) || !state.fg_feature) {
        state.fg_feature = nullptr;
        ReleaseFrameGenLocked();
        return 0;
    }
    state.fg_desc = *desc;
    Log(0, "DLSS Frame Generation %ux%u, render %ux%u, format %d", desc->width, desc->height,
        desc->render_width, desc->render_height, static_cast<int>(desc->format));
    return 1;
}

void CopyMatrix(float (&to)[4][4], const float (&from)[16]) {
    std::memcpy(to, from, sizeof(to));
}

NVSDK_NGX_Dimensions Subrect(const BbDlssImage& image, uint32_t width, uint32_t height) {
    // Depth and motion vectors may sit in larger images than the render size: the active part.
    return image.width == width && image.height == height ? NVSDK_NGX_Dimensions{0, 0}
                                                          : NVSDK_NGX_Dimensions{width, height};
}

int32_t EvaluateFrameGen(VkCommandBuffer command, const BbDlssFrameGenEvaluate* evaluate) {
    std::scoped_lock lock{state.mutex};
    if (!state.fg_feature || !state.fg_parameters || !evaluate)
        return 0;
    auto backbuffer = Wrap(evaluate->backbuffer, false);
    auto depth = Wrap(evaluate->depth, false);
    auto motion = Wrap(evaluate->motion, false);
    auto hudless = Wrap(evaluate->hudless, false);
    auto output = Wrap(evaluate->output, true);
    NVSDK_NGX_VK_DLSSG_Eval_Params resources{};
    resources.pBackbuffer = &backbuffer;
    resources.pDepth = &depth;
    resources.pMVecs = &motion;
    resources.pHudless = evaluate->hudless.image != VK_NULL_HANDLE ? &hudless : nullptr;
    resources.pOutputInterpFrame = &output;

    NVSDK_NGX_DLSSG_Opt_Eval_Params options{};
    options.multiFrameCount = 1; // one generated frame between two game frames
    options.multiFrameIndex = 1;
    CopyMatrix(options.cameraViewToClip, evaluate->view_to_clip);
    CopyMatrix(options.clipToCameraView, evaluate->clip_to_view);
    CopyMatrix(options.clipToPrevClip, evaluate->clip_to_prev_clip);
    CopyMatrix(options.prevClipToClip, evaluate->prev_clip_to_clip);
    for (int i = 0; i < 4; ++i)
        options.clipToLensClip[i][i] = 1.0f; // no lens distortion
    options.jitterOffset[0] = evaluate->jitter_x;
    options.jitterOffset[1] = evaluate->jitter_y;
    options.mvecScale[0] = options.mvecScale[1] = 1.0f; // already pixels
    std::memcpy(options.cameraPos, evaluate->camera_pos, sizeof(options.cameraPos));
    std::memcpy(options.cameraUp, evaluate->camera_up, sizeof(options.cameraUp));
    std::memcpy(options.cameraRight, evaluate->camera_right, sizeof(options.cameraRight));
    std::memcpy(options.cameraFwd, evaluate->camera_forward, sizeof(options.cameraFwd));
    options.cameraNear = evaluate->camera_near;
    options.cameraFar = evaluate->camera_far;
    options.cameraFOV = evaluate->camera_fov;
    options.cameraAspectRatio = evaluate->camera_aspect;
    options.colorBuffersHDR = false; // SDR output only
    options.depthInverted = evaluate->depth_inverted != 0;
    options.cameraMotionIncluded = true;
    options.reset = evaluate->reset != 0;
    // Every pixel of the motion vectors is written: no value stands for "not written".
    options.motionVectorsInvalidValue = FLT_MAX;
    options.motionVectorsDilated = false;
    options.mvecsSubrectSize = Subrect(evaluate->motion, state.fg_desc.render_width,
                                       state.fg_desc.render_height);
    options.depthSubrectSize = Subrect(evaluate->depth, state.fg_desc.render_width,
                                       state.fg_desc.render_height);
    return Check("DLSS Frame Generation evaluation",
                 NGX_VK_EVALUATE_DLSSG(command, state.fg_feature, state.fg_parameters, &resources,
                                       &options))
               ? 1
               : 0;
}

void Shutdown() {
    std::scoped_lock lock{state.mutex};
    ReleaseFrameGenLocked();
    ReleaseFeatureLocked();
    if (state.capabilities) {
        NVSDK_NGX_VULKAN_DestroyParameters(state.capabilities);
        state.capabilities = nullptr;
    }
    if (state.initialized) {
        NVSDK_NGX_VULKAN_Shutdown1(state.device);
        state.initialized = false;
        state.device = VK_NULL_HANDLE;
    }
}

const BbDlssApi api{BBPORT_DLSS_BRIDGE_ABI, Configure,     InstanceExtensions, DeviceExtensions,
                    Initialize,             CreateFeature, Evaluate,           ReleaseFeature,
                    Shutdown,               FrameGenAvailable, CreateFrameGen, EvaluateFrameGen,
                    ReleaseFrameGen};

} // namespace

extern "C" __declspec(dllexport) const BbDlssApi* BbDlssGetApi() {
    return &api;
}
