// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: the camera DLSS frame generation wants (docs: README-Windows.txt), built from the game's
// view and projection. No Vulkan: tests/test_fg_camera.cpp checks it against camera_motion.comp,
// the ground truth of the camera model:
//   clip = (xs * vx, ys * vy, zs * vz + zo, vz), depth = zs + zo / vz (0 near, 1 far, not
//   inverted), ndc +y DOWN the screen (ys carries the viewport's sign), vz > 0 in front,
//   previous view space = previous.view * current.inv_view.
// NGX expects D3D clip space with +y UP, so every clip space matrix is wrapped in the y flip F.

#pragma once

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace Vulkan::FgCamera {

/// The far plane of the scene constants (the AMD frame generation gets the same).
inline constexpr float kCameraFar = 3000.0f;

struct Camera {
    std::array<float, 12> view;     ///< world to view, 3x4 rows
    std::array<float, 12> inv_view; ///< view to world, 3x4 rows
    std::array<float, 4> proj;      ///< x scale, y scale, z scale, z offset
};

/// Row-major, for row vectors (v * M), the D3D convention NGX expects.
using Mat4 = std::array<float, 16>;

struct Matrices {
    Mat4 view_to_clip{}, clip_to_view{}, clip_to_prev_clip{}, prev_clip_to_clip{};
    /// World space; unit directions.
    std::array<float, 3> pos{}, up{}, right{}, forward{};
    float near_plane = 0.0f, far_plane = kCameraFar, fov = 0.0f, aspect = 1.0f; ///< fov in radians
    std::array<float, 2> jitter{}; ///< clip space, no jitter in the matrices
};

/// The y axis of the clip space the matrices are built for, relative to the game's ndc (+y down
/// the screen): -1 flips it to D3D's +y up, which NGX expects. BB_DLSS_FG_CLIP_Y=down keeps the
/// game's direction (tests: whether the flip is what the image needs).
inline float ClipYSign() {
    static const float sign = [] {
        const char* env = std::getenv("BB_DLSS_FG_CLIP_Y");
        return env && std::strcmp(env, "down") == 0 ? 1.0f : -1.0f;
    }();
    return sign;
}

namespace detail {

using M4 = std::array<double, 16>; ///< row-major, column vectors (M * v) while building

inline M4 Multiply(const M4& a, const M4& b) {
    M4 out{};
    for (int i = 0; i < 4; ++i) {
        for (int j = 0; j < 4; ++j) {
            for (int k = 0; k < 4; ++k) {
                out[i * 4 + j] += a[i * 4 + k] * b[k * 4 + j];
            }
        }
    }
    return out;
}

/// A 3x4 affine matrix (rows [R | t]) as 4x4.
inline M4 Affine(const std::array<float, 12>& m) {
    M4 out{};
    for (int i = 0; i < 12; ++i) {
        out[i] = m[i];
    }
    out[15] = 1.0;
    return out;
}

/// Current view space to clip: the projection the motion shader uses (w = view z).
inline M4 Projection(const std::array<float, 4>& p) {
    return {p[0], 0, 0, 0, 0, p[1], 0, 0, 0, 0, p[2], p[3], 0, 0, 1, 0};
}

inline M4 InverseProjection(const std::array<float, 4>& p) {
    return {1.0 / p[0], 0, 0, 0, 0, 1.0 / p[1], 0, 0, 0, 0, 0, 1, 0, 0, 1.0 / p[3],
            -double(p[2]) / p[3]};
}

inline M4 Flip(double y) {
    return {1, 0, 0, 0, 0, y, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
}

/// Column vector matrix to the row vector storage NGX reads.
inline Mat4 ToRows(const M4& m) {
    Mat4 out{};
    for (int i = 0; i < 4; ++i) {
        for (int j = 0; j < 4; ++j) {
            out[i * 4 + j] = float(m[j * 4 + i]);
        }
    }
    return out;
}

inline std::array<float, 3> Direction(const std::array<float, 12>& m, int column, double sign) {
    const double x = sign * m[column], y = sign * m[4 + column], z = sign * m[8 + column];
    const double length = std::sqrt(x * x + y * y + z * z);
    return length > 0.0 ? std::array<float, 3>{float(x / length), float(y / length), float(z / length)}
                        : std::array<float, 3>{};
}

} // namespace detail

/// The matrices and basis of the frame `current`, with the frame before it as `previous`.
/// `jitter` is the viewport shift of the frame in render pixels (x right, y down); it is not in
/// the matrices.
inline Matrices Build(const Camera& current, const Camera& previous,
                      std::array<uint32_t, 2> render_size, std::array<float, 2> jitter,
                      float y_sign = ClipYSign()) {
    using namespace detail;
    const M4 flip = Flip(y_sign);
    const M4 projection = Projection(current.proj);
    const M4 previous_projection = Projection(previous.proj);
    const M4 to_previous = Multiply(
        previous_projection,
        Multiply(Multiply(Affine(previous.view), Affine(current.inv_view)),
                 InverseProjection(current.proj)));
    const M4 to_current = Multiply(
        projection, Multiply(Multiply(Affine(current.view), Affine(previous.inv_view)),
                             InverseProjection(previous.proj)));
    Matrices out;
    out.view_to_clip = ToRows(Multiply(flip, projection));
    out.clip_to_view = ToRows(Multiply(InverseProjection(current.proj), flip));
    out.clip_to_prev_clip = ToRows(Multiply(flip, Multiply(to_previous, flip)));
    out.prev_clip_to_clip = ToRows(Multiply(flip, Multiply(to_current, flip)));

    // inv_view's columns are the view axes in world space; ndc x grows with view x when the x
    // scale is positive, and the screen's up is view -y when the y scale is positive (ndc +y down).
    const double x_sign = current.proj[0] < 0.0f ? -1.0 : 1.0;
    const double y_screen_sign = current.proj[1] < 0.0f ? -1.0 : 1.0;
    out.right = Direction(current.inv_view, 0, x_sign);
    out.up = Direction(current.inv_view, 1, -y_screen_sign);
    out.forward = Direction(current.inv_view, 2, 1.0);
    out.pos = {current.inv_view[3], current.inv_view[7], current.inv_view[11]};
    out.near_plane = -current.proj[3] / current.proj[2];
    out.far_plane = kCameraFar;
    out.fov = 2.0f * std::atan(1.0f / std::abs(current.proj[1]));
    out.aspect = std::abs(current.proj[1] / current.proj[0]);
    out.jitter = {2.0f * jitter[0] / float(render_size[0]),
                  y_sign * 2.0f * jitter[1] / float(render_size[1])};
    return out;
}

} // namespace Vulkan::FgCamera
