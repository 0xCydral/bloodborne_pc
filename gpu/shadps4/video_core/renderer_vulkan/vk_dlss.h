// SPDX-FileCopyrightText: Copyright 2026 IFreemz, bbport contributors
// SPDX-License-Identifier: GPL-2.0-or-later
//
// bbport: NVIDIA DLSS Super Resolution through the optional bbport_dlss.dll bridge (gpu/
// dlss_bridge, MIT), loaded at run time from next to bb-probe.exe together with NVIDIA's
// nvngx_dlss.dll. The port itself contains no NVIDIA code; without the DLLs, on other GPUs or
// with BB_DLSS=0 nothing changes. Adapted from IFreemz/shadPS4-Bloodborne-DLSS-FSR
// (vk_dlss_ngx). DLSS Frame Generation (bridge abi 2, NVIDIA's nvngx_dlssg.dll) goes through the
// same bridge; vk_frame_generation.cpp decides when it runs.

#pragma once

#include <memory>
#include <string>
#include <vector>

#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/renderer_vulkan/vk_fg_camera.h"

namespace Vulkan {

class Dlss {
public:
    /// The process-wide instance, or null when the DLLs are absent or BB_DLSS=0.
    static Dlss* Get();
    ~Dlss();

    /// Instance and device creation: adds what NGX needs (or disables DLSS when it is missing).
    void AppendInstanceExtensions(std::vector<const char*>& enabled);
    void AppendDeviceExtensions(vk::Instance instance, vk::PhysicalDevice physical,
                                std::vector<const char*>& enabled);
    /// After the device exists.
    void Initialize(vk::Instance instance, vk::PhysicalDevice physical, vk::Device device);
    void Shutdown();

    [[nodiscard]] bool Available() const;
    /// Why DLSS cannot run on this system (empty when it can, or before device creation).
    [[nodiscard]] std::string Problem() const;

    struct Resource {
        vk::Image image;
        vk::ImageView view;
        vk::Format format;
        vk::ImageAspectFlags aspect;
        u32 width, height;
    };
    struct FeatureDesc {
        u32 input_width, input_height, output_width, output_height;
        int quality;
        bool hdr;
        bool operator==(const FeatureDesc&) const = default;
    };
    struct Frame {
        Resource color, depth, motion, output;
        float jitter_x, jitter_y;
        bool reset;
        float frame_ms;
        float sharpness;
    };

    /// DLSS quality mode for an output/render size ratio (0 DLAA .. 4 Ultra Performance).
    static int QualityForScale(float scale);
    [[nodiscard]] bool HasFeature(const FeatureDesc& desc) const;
    /// Records the feature creation. The GPU must be done with the previous one.
    bool CreateFeature(vk::CommandBuffer command, const FeatureDesc& desc);
    bool Evaluate(vk::CommandBuffer command, const Frame& frame);
    void ReleaseFeature();

    // ---- DLSS Frame Generation (GeForce RTX 40 and newer) ------------------------------------

    /// After Initialize: NGX can run it here (bridge abi 2, nvngx_dlssg.dll, an RTX 40 or newer
    /// GPU, a current driver, hardware-accelerated GPU scheduling, all Vulkan extensions).
    [[nodiscard]] bool FrameGenerationAvailable() const;
    /// Why it cannot (empty when it can, or before Initialize).
    [[nodiscard]] std::string FrameGenerationProblem() const;

    struct FrameGenDesc {
        u32 width, height; ///< the backbuffer and the interpolated frame
        vk::Format format; ///< the backbuffer's format
        u32 render_width, render_height; ///< the depth and motion vectors
        bool operator==(const FrameGenDesc&) const = default;
    };
    struct FrameGenFrame {
        Resource backbuffer; ///< this frame as it is shown, with the UI
        Resource depth, motion;
        Resource hudless;    ///< the same without the UI; a null image leaves it out
        Resource output;     ///< storage, the interpolated frame
        const FgCamera::Matrices* camera;
        bool reset;
    };
    /// Records the feature creation (`command` stays open). The GPU must be done with the previous
    /// one.
    bool CreateFrameGeneration(vk::CommandBuffer command, const FrameGenDesc& desc);
    bool EvaluateFrameGeneration(vk::CommandBuffer command, const FrameGenFrame& frame);
    void ReleaseFrameGeneration();

private:
    Dlss();
    void DetectFrameGeneration();
    struct Impl;
    std::unique_ptr<Impl> impl;
};

} // namespace Vulkan
