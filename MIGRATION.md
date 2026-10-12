# Migration notes (Windows wipe, move to Pop!_OS 24.04)

Written 2026-10-12, before C: was formatted. Covers this repo and the sibling repo
`AMDLSS` (https://github.com/0xCydral/AMDLSS), which was built in the same session.

## State

- Done and published:
  - HelixSR (DLSS's network on any GPU) as an experimental Windows upscaler, through a
    Vulkan to Direct3D 12 bridge (`gpu/shadps4/video_core/renderer_vulkan/vk_helixsr.*`),
    plus a one-click "Install HelixSR" button in the Windows launcher.
  - Released as `windows-v1.8.0-beta.1` (pre-release). `develop` and `master` are pushed.
  - AMDLSS 0.1.0-beta.4 published (HelixSR + OptiScaler installer for other games,
    Collect logs, self-update). Its `main` is pushed.
- Half-done:
  - Waiting on a tester's logs (AMDLSS "Collect logs" zip) for Final Fantasy XVI and
    Dragon's Dogma 2, where HelixSR did not seem to take effect.
  - AMDLSS window redesign: concept images from Codex image generation failed on the
    ChatGPT usage limit. Nothing was generated. The HTML mockups in
    `AMDLSS/design/concepts/` were rejected.
  - Release checklist steps not run for 1.8.0-beta.1: the Vulkan validation layer run
    and the updater check from an older launcher.
- Exact next step: rerun the AMDLSS image-concept task (3 concepts, GPT Image
  generation only) once the Codex limit resets, then build the one the owner picks.
- Feature doc for the HelixSR work: `odd/tasks/helixsr-vulkan-bridge.md`.

## Must keep (cannot regenerate)

| Path | Size | Note |
|---|---|---|
| `C:\Games\CUSA03173` | 29,972 MB | Bloodborne game dump (CUSA03173, update 1.09). Outside Projects, so `backup.ps1` does not copy it. Copied on 2026-10-12 to `D:\migration\Games\CUSA03173` (28,810 files, 31,428,130,448 bytes, identical counts to the source). |
| `user/savedata` | 14.3 MB | Game saves. Inside the repo, gitignored, copied by `backup.ps1`. |
| `gpu/third_party/fsr-vulkan` local edits | 3 files | Uncommitted edits from 2026-10-08 in the submodule (`ffx_vk_fsr4_v07.*`, `ffx_vk.cpp`). Not mine; only the D: copy keeps them. |

## Safe to skip in the copy

| Path | Size | Rebuild |
|---|---|---|
| `out/` | 2,257 MB | `./build.sh` (Linux) or `source ./msys2-env.sh && ./build.sh` (Windows) |
| `dist/` | 974 MB | `packaging/windows/package.sh` (Windows only) |
| `helixsr/` | 223 MB | Launcher: Advanced > Install HelixSR (Windows only) |
| `fsr4vk/` | 17 MB | Launcher download, or `python tools/fetch_fsr4vk.py` |
| `user/cache` | 31 MB | Rebuilt on the next game start |
| `*.spv` in the repo root | small | Shader build output, rebuilt by `./build.sh` |
| `AMDLSS/out`, `AMDLSS/dist` | 54 MB | `bash packaging/build.sh` in AMDLSS (Windows only) |

## Outside this repo

- `C:\Games\CUSA03173` (game files, see above).
- `C:\Users\WinterOS\HelixSR` (277 MB): HelixSR 1.8.1 test copy, re-downloadable.
- `%LOCALAPPDATA%\HelixSR` (100 MB): HelixSR's Python, numpy and DXC, re-downloaded by its setup.
- `%LOCALAPPDATA%\AMDLSS` (223 MB): AMDLSS's HelixSR copy, settings and logs, re-creatable.
- `C:\msys64` (MSYS2 CLANG64 toolchain for the Windows build, see `msys2-env.sh`).
- GitHub CLI login (`gh auth`), needed to publish releases.
- No Docker containers, scheduled tasks, ports or `.env` files are used by this project.

## Linux port

The game itself already runs on Linux (upstream port, `run.sh`, GTK launcher
`launcher/bbport_launcher.py`, `build.sh`). Windows-only parts:

- HelixSR backend (`vk_helixsr.cpp`): Direct3D 12 + `amd_fidelityfx_dx12.dll`. Cannot run
  natively on Linux. Under Proton/Wine it would need vkd3d-proton plus Vulkan/D3D12
  interop; untested. Code is `#ifdef _WIN32`, so the Linux build is unaffected.
- Windows launcher (`launcher/bbport_launcher_win.py`), packaging (`packaging/windows/`),
  `msys2-env.sh`: Windows only. Use the GTK launcher on Linux.
- DLSS, XeSS and fsr4vk Windows paths: the Linux build has its own FSR 4.1.1 route.
- 24 hardcoded `C:\` references in 15 files (`run.py`, the Windows launcher, tests, the
  feature doc). Most are test fixtures or Windows defaults; `run.py` needs `BB_GAME_DIR`.
- AMDLSS is Windows only (installs into Windows game folders, PyInstaller exe,
  25 `C:\` references in tests). On Linux it would only make sense for Proton games;
  not planned.
- RTX 4070: install the NVIDIA driver (Pop!_OS NVIDIA ISO) and `vulkan-tools`; no CUDA needed.

## Resume on Linux

```bash
git clone https://github.com/0xCydral/bloodborne_pc && cd bloodborne_pc
git checkout develop
git submodule update --init --recursive
# restore the local submodule edits and saves from the D: copy if wanted:
#   D:\migration\Projects\bloodborne_pc\gpu\third_party\fsr-vulkan
#   D:\migration\Projects\bloodborne_pc\user\savedata
nix-shell   # repo-root shell.nix; or install the build deps the upstream Linux port lists
./build.sh
BB_GAME_DIR=/path/to/CUSA03173/uroot ./run.sh
git clone https://github.com/0xCydral/AMDLSS   # Windows-only app; keep for reference
```

HelixSR testing and AMDLSS work need the Windows side of the dual boot.
