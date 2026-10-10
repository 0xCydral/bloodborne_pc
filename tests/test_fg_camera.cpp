// SPDX-License-Identifier: GPL-2.0-or-later
// DLSS frame generation camera (vk_fg_camera.h): the clip space matrices built from the game's
// view and projection agree with the reprojection of camera_motion.comp (the ground truth of the
// camera model), invert each other, and the camera basis vectors are unit length and point the
// way the screen does. Pure math, no Vulkan.
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>

#include "video_core/renderer_vulkan/vk_fg_camera.h"

namespace {

using Vulkan::FgCamera::Camera;
using Vulkan::FgCamera::Matrices;
using Vulkan::FgCamera::Mat4;

int failures = 0;

void Check(bool ok, const char* what, double a = 0.0, double b = 0.0) {
    if (!ok) {
        if (++failures <= 12) {
            std::printf("FAIL: %s (%.7g vs %.7g)\n", what, a, b);
        }
    }
}

bool Near(double a, double b, double tolerance) {
    return std::abs(a - b) <= tolerance * (1.0 + std::abs(a) + std::abs(b));
}

/// A rotation matrix from an axis and an angle (Rodrigues).
std::array<std::array<double, 3>, 3> Rotation(std::mt19937& rng, double max_angle) {
    std::uniform_real_distribution<double> unit(-1.0, 1.0);
    double axis[3] = {unit(rng), unit(rng), unit(rng)};
    const double length = std::sqrt(axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2]);
    for (double& a : axis) {
        a /= length;
    }
    const double angle = unit(rng) * max_angle;
    const double c = std::cos(angle), s = std::sin(angle), t = 1.0 - c;
    return {{{t * axis[0] * axis[0] + c, t * axis[0] * axis[1] - s * axis[2],
              t * axis[0] * axis[2] + s * axis[1]},
             {t * axis[0] * axis[1] + s * axis[2], t * axis[1] * axis[1] + c,
              t * axis[1] * axis[2] - s * axis[0]},
             {t * axis[0] * axis[2] - s * axis[1], t * axis[1] * axis[2] + s * axis[0],
              t * axis[2] * axis[2] + c}}};
}

/// The game's 3x4 world to view matrix [R | t] with the exact inverse for view to world, and a
/// D3D projection. With `base`, a camera a frame later: turned and moved a little (consecutive
/// frames are close, which is what keeps the clip space matrices well conditioned).
Camera MakeCamera(std::mt19937& rng, float aspect, const Camera* base = nullptr) {
    std::uniform_real_distribution<double> unit(-1.0, 1.0);
    auto r = Rotation(rng, base ? 0.05 : 3.1);
    double move[3] = {unit(rng) * 40.0, unit(rng) * 40.0, unit(rng) * 40.0};
    if (base) {
        std::array<std::array<double, 3>, 3> turned{};
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j) {
                for (int k = 0; k < 3; ++k) {
                    turned[i][j] += r[i][k] * base->view[k * 4 + j];
                }
            }
            move[i] = base->view[i * 4 + 3] + unit(rng) * 0.3;
        }
        r = turned;
    }
    Camera camera{};
    for (int i = 0; i < 3; ++i) {
        for (int j = 0; j < 3; ++j) {
            camera.view[i * 4 + j] = float(r[i][j]);
            camera.inv_view[i * 4 + j] = float(r[j][i]); // transpose
        }
        camera.view[i * 4 + 3] = float(move[i]);
        camera.inv_view[i * 4 + 3] =
            float(-(r[0][i] * move[0] + r[1][i] * move[1] + r[2][i] * move[2]));
    }
    // D3D style: depth = zs + zo / z, 0 at the near plane, 1 at the far plane.
    const double near_plane = 0.1 + 0.1 * std::abs(unit(rng)), far_plane = 3000.0;
    const double fov = 0.6 + 0.5 * std::abs(unit(rng));
    const double ys_sign = unit(rng) < 0.0 ? -1.0 : 1.0; // the viewport decides, per frame
    const double ys = ys_sign / std::tan(fov * 0.5);
    camera.proj = {float(std::abs(ys) / aspect), float(ys),
                   float(far_plane / (far_plane - near_plane)),
                   float(-near_plane * far_plane / (far_plane - near_plane))};
    return camera;
}

/// 3x4 affine times a point (w = 1).
std::array<double, 3> Apply(const std::array<float, 12>& m, const std::array<double, 3>& p) {
    std::array<double, 3> out{};
    for (int i = 0; i < 3; ++i) {
        out[i] = m[i * 4 + 0] * p[0] + m[i * 4 + 1] * p[1] + m[i * 4 + 2] * p[2] + m[i * 4 + 3];
    }
    return out;
}

/// row vector times row-major matrix: v * M.
std::array<double, 4> Row(const std::array<double, 4>& v, const Mat4& m) {
    std::array<double, 4> out{};
    for (int j = 0; j < 4; ++j) {
        for (int i = 0; i < 4; ++i) {
            out[j] += v[i] * m[i * 4 + j];
        }
    }
    return out;
}

void CheckIdentity(const Mat4& a, const Mat4& b, const char* what) {
    for (int i = 0; i < 4; ++i) {
        for (int j = 0; j < 4; ++j) {
            double sum = 0.0;
            for (int k = 0; k < 4; ++k) {
                sum += double(a[i * 4 + k]) * double(b[k * 4 + j]);
            }
            Check(std::abs(sum - (i == j ? 1.0 : 0.0)) < 2e-3, what, sum, i == j ? 1.0 : 0.0);
        }
    }
}

double Length(const std::array<float, 3>& v) {
    return std::sqrt(double(v[0]) * v[0] + double(v[1]) * v[1] + double(v[2]) * v[2]);
}

double Dot(const std::array<float, 3>& a, const std::array<float, 3>& b) {
    return double(a[0]) * b[0] + double(a[1]) * b[1] + double(a[2]) * b[2];
}

void RunPair(std::mt19937& rng, float y_sign, bool close) {
    std::uniform_real_distribution<double> unit(-1.0, 1.0);
    const float aspect = 1.2f + 0.8f * float(std::abs(unit(rng)));
    const Camera current = MakeCamera(rng, aspect);
    const Camera previous = MakeCamera(rng, aspect, close ? &current : nullptr);
    const std::array<uint32_t, 2> render{1280, 720};
    const std::array<float, 2> jitter{0.37f, -0.21f};
    const Matrices m = Vulkan::FgCamera::Build(current, previous, render, jitter, y_sign);

    CheckIdentity(m.view_to_clip, m.clip_to_view, "view_to_clip * clip_to_view");
    CheckIdentity(m.clip_to_view, m.view_to_clip, "clip_to_view * view_to_clip");
    if (close) {
        // Two float matrices whose entries are in the hundreds only invert each other to about
        // 1e-7 times their product: frames far apart would need a looser bound than a frame.
        CheckIdentity(m.clip_to_prev_clip, m.prev_clip_to_clip,
                      "clip_to_prev_clip * prev_clip_to_clip");
        CheckIdentity(m.prev_clip_to_clip, m.clip_to_prev_clip,
                      "prev_clip_to_clip * clip_to_prev_clip");
    }

    for (int n = 0; n < 40; ++n) {
        // A point in front of the current camera, in its view space.
        const double z = 0.5 + std::abs(unit(rng)) * 60.0;
        const std::array<double, 3> view{unit(rng) * z * 0.8, unit(rng) * z * 0.5, z};

        // The shader (camera_motion.comp): ndc +y down, reproject = previous.view * inverse view.
        const auto world = Apply(current.inv_view, view);
        const auto prev_view = Apply(previous.view, world);
        const double shader_x = prev_view[0] * previous.proj[0] / prev_view[2];
        const double shader_y = prev_view[1] * previous.proj[1] / prev_view[2];
        const double shader_depth = previous.proj[2] + previous.proj[3] / prev_view[2];
        if (prev_view[2] < 0.2) {
            continue; // behind the previous camera: no meaningful clip position
        }

        // The matrices: the current clip point (y flipped as the matrices expect), moved to the
        // previous clip space, divided, the flip undone.
        const std::array<double, 4> clip_down{current.proj[0] * view[0], current.proj[1] * view[1],
                                              current.proj[2] * view[2] + current.proj[3], view[2]};
        const std::array<double, 4> clip{clip_down[0], clip_down[1] * y_sign, clip_down[2],
                                         clip_down[3]};
        const auto prev = Row(clip, m.clip_to_prev_clip);
        Check(Near(prev[3], prev_view[2], 2e-3), "previous clip w is the previous view z", prev[3],
              prev_view[2]);
        Check(Near(prev[0] / prev[3], shader_x, 2e-3), "prev ndc x", prev[0] / prev[3], shader_x);
        Check(Near(prev[1] / prev[3] * y_sign, shader_y, 2e-3), "prev ndc y",
              prev[1] / prev[3] * y_sign, shader_y);
        Check(Near(prev[2] / prev[3], shader_depth, 2e-3), "prev depth", prev[2] / prev[3],
              shader_depth);

        // And back: the previous clip point returns to the current one.
        if (close) {
            const auto back = Row(prev, m.prev_clip_to_clip);
            for (int i = 0; i < 4; ++i) {
                Check(Near(back[i], clip[i], 2e-3), "prev_clip_to_clip returns the clip point",
                      back[i], clip[i]);
            }
        }

        // view_to_clip and clip_to_view are the same projection.
        const auto projected = Row({view[0], view[1], view[2], 1.0}, m.view_to_clip);
        for (int i = 0; i < 4; ++i) {
            Check(Near(projected[i], clip[i], 2e-3), "view_to_clip is the projection", projected[i],
                  clip[i]);
        }

        // The camera basis is the way the screen goes: a point right of and above the optical
        // axis lands right of and above the centre (ndc +y is down in the shader).
        const std::array<double, 3> target{
            m.pos[0] + 6.0 * m.forward[0] + 0.7 * m.right[0] + 0.4 * m.up[0],
            m.pos[1] + 6.0 * m.forward[1] + 0.7 * m.right[1] + 0.4 * m.up[1],
            m.pos[2] + 6.0 * m.forward[2] + 0.7 * m.right[2] + 0.4 * m.up[2]};
        const auto target_view = Apply(current.view, target);
        Check(target_view[2] > 0.0, "forward points into the scene", target_view[2], 0.0);
        Check(current.proj[0] * target_view[0] / target_view[2] > 0.0, "right is screen right",
              current.proj[0] * target_view[0] / target_view[2], 0.0);
        Check(current.proj[1] * target_view[1] / target_view[2] < 0.0, "up is screen up",
              current.proj[1] * target_view[1] / target_view[2], 0.0);
    }

    Check(Near(Length(m.up), 1.0, 1e-5), "up is a unit vector", Length(m.up), 1.0);
    Check(Near(Length(m.right), 1.0, 1e-5), "right is a unit vector", Length(m.right), 1.0);
    Check(Near(Length(m.forward), 1.0, 1e-5), "forward is a unit vector", Length(m.forward), 1.0);
    Check(std::abs(Dot(m.right, m.forward)) < 1e-4, "right is perpendicular to forward",
          Dot(m.right, m.forward), 0.0);
    Check(std::abs(Dot(m.up, m.forward)) < 1e-4, "up is perpendicular to forward",
          Dot(m.up, m.forward), 0.0);
    for (int i = 0; i < 3; ++i) {
        Check(Near(m.pos[i], current.inv_view[i * 4 + 3], 1e-6), "position is the view to world translation",
              m.pos[i], current.inv_view[i * 4 + 3]);
    }

    const double near_plane = -double(current.proj[3]) / current.proj[2];
    Check(Near(m.near_plane, near_plane, 1e-5), "near plane", m.near_plane, near_plane);
    Check(m.far_plane == 3000.0f, "far plane is the constant 3000", m.far_plane, 3000.0);
    Check(Near(m.fov, 2.0 * std::atan(1.0 / std::abs(double(current.proj[1]))), 1e-5),
          "vertical fov", m.fov, 0.0);
    Check(Near(m.aspect, std::abs(double(current.proj[1]) / current.proj[0]), 1e-5), "aspect",
          m.aspect, 0.0);

    // Jitter in clip space: pixels over half the size, the y sign of the clip convention.
    Check(Near(m.jitter[0], 2.0 * jitter[0] / render[0], 1e-6), "clip jitter x", m.jitter[0], 0.0);
    Check(Near(m.jitter[1], y_sign * 2.0 * jitter[1] / render[1], 1e-6), "clip jitter y",
          m.jitter[1], 0.0);
}

} // namespace

int main() {
    // The default convention is the y flip (D3D clip space: +y up, the game's pixel rows go down).
    if (!std::getenv("BB_DLSS_FG_CLIP_Y")) {
        Check(Vulkan::FgCamera::ClipYSign() == -1.0f, "default clip y sign is the flip",
              Vulkan::FgCamera::ClipYSign(), -1.0);
    }
    std::mt19937 rng(12345);
    for (int i = 0; i < 25; ++i) {
        for (const bool close : {true, false}) {
            RunPair(rng, -1.0f, close);
            RunPair(rng, 1.0f, close);
        }
    }
    if (failures) {
        std::printf("fg-camera-test: %d checks failed\n", failures);
        return 1;
    }
    std::printf("fg-camera-test: ok\n");
    return 0;
}
