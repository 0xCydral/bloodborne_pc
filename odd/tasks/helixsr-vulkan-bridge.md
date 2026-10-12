# HelixSR on the Vulkan renderer (experimental)

## Objective

Offer HelixSR (NVIDIA's DLSS Model E network run as D3D12 compute shaders, github.com/lonewolf0622/HelixSR)
as an experimental upscaler on Windows, mainly for AMD cards that cannot run DLSS.

## Problem and why

HelixSR ships only as a closed Direct3D 12 DLL that speaks AMD's FidelityFX (FSR 3.1) API. The port renders with
Vulkan. The DLL cannot be ported or modified (HelixSR Freeware License: no modification, no reverse engineering) and
its network is NVIDIA's (built on the player's PC, must not be shared). The only lawful route is to call the
player's own, unmodified DLL from the Vulkan renderer through a D3D12 device on the same GPU, with images and a
fence shared between the two APIs.

## Scope

- In: a Windows-only bridge (D3D12 device on the Vulkan device's adapter, shared images, shared fence, FFX API
  calls into the player's `amd_fidelityfx_dx12.dll`); a new upscaler choice that uses it; launcher and in-game menu
  entries marked experimental; docs.
- Out: shipping or downloading HelixSR or NVIDIA files; running HelixSR's setup for the player; Linux; frame
  generation through HelixSR (the port's own frame generation keeps working on the upscaled image).

## Constraints

- Never bundle, download or redistribute HelixSR or anything NVIDIA. The player installs HelixSR and points the
  launcher at it.
- No change to the existing upscaler paths when HelixSR is not selected.
- If the bridge fails at any step, log the reason and fall back to FSR 3.1 instead of crashing.

## Tasks

- [x] T1 Bridge core and standalone check: Vulkan -> D3D12 -> FFX dispatch -> Vulkan round trip, proven with AMD's
      FSR 3.1 DX12 DLL (control) and with HelixSR's DLL. Route: delegated writer (2+ non-trivial new files).
      Commit 3234383. Evidence (re-run by the parent): `helixsr-bridge-test` PASS with HelixSR 1.8.1
      (max_err 0.0016; helixsr.log: "network running (nchw8, 20 launches per frame)") and with AMD FSR 4.1.1
      (max_err 0.0017) on the RTX 4070; settings-test and upscaler-support-test pass. Review: medium risk, due
      (slice budget), consent granted, lineage review-c5ad57d9ed7c2719 approved and acknowledged (reviewed
      boundary now 3234383). Its five non-blocking findings are being fixed as T1b.
- [x] T1b Review fixes: first-frame output acquire, Dispatch HRESULT and bookkeeping, no release after a failed
      wait, failure-path tests, Configure validates depth and motion formats. Route: same writer, resumed.
      Commit 85b39fe. Evidence (parent re-run): 16/16 cases with HelixSR (max_err 0.0016; reconfigured
      960x540 0.0021) and with AMD FSR 4.1.1. Review: medium, due (581 lines), consent granted, lineage
      review-e237eb1aa789046c approved and acknowledged (boundary now 85b39fe); its two warnings fixed in T1c.
- [x] T1c Second review's fixes: device removal seen in a fence wait marks the bridge broken; Destroy returns early
      when it owns nothing; cases for Dispatch above any issued value and repeated Destroy. Commit d15d339.
      Evidence: 20/20 cases with HelixSR (parent re-run). Review: under budget (28 lines), pending in the next slice.
- [x] T2 Game integration: upscaler choice, render size and jitter from the port (Halton, ForPreset; the DLL's
      queries are not needed), per-frame submit split around the D3D12 dispatch, fallback to FSR 3.1.
      Route: delegated writer. Commit c1f6536. Evidence (parent re-run): settings-test, upscaler-support-test,
      bridge test 20/20 pass; startup log "Upscaler: HelixSR selected"; writer's gameplay session logged
      "bridge initialised", "Upscaler: HelixSR 1280x720->1920x1080", helixsr.log dispatches 0-600 at
      1280x720 -> 1920x1080, no fallback. Native AA path (preset 0) not exercised yet. Review: under budget
      (366 lines since 85b39fe), pending. Incident: the writer closed the user's play session with
      CloseMainWindow at 12:08 (autosave 12:07); rule since: never close a game the user is playing.
- [x] T3 Settings, launcher and in-game menu: HelixSR folder setting, availability with a reason, experimental label.
      Route: delegated writer. Commit 0270f34 (+ packaging/windows/package.sh keeps `helixsr` on update).
      Evidence (parent re-run): `python -m pytest -q tests` 452 passed, 45 skipped; `bb-gpu-capabilities
      --upscalers` prints `helixsr supported` on the RTX 4070. Review of d15d339..0270f34: high risk, four
      lenses, consent granted, lineage review-42e4151e85577295 approved and acknowledged (boundary now 0270f34);
      its findings are being fixed as T3b.
- [x] T3b Four-lens review fixes: no C:\HelixSR fallback (DLL planting), version-ordered HelixSR-* subfolders,
      stale BB_HELIXSR_DIR removed, EXPERIMENTAL_REASON shared name, renderer reason strings tested, runtime
      failure greys HelixSR out with its reason (no retry loop), far-plane comment, exe path without MAX_PATH.
      Route: the T3 writer, resumed. Commit d6581d8. Evidence (parent re-run): 457 Python tests pass,
      settings-test and upscaler-support-test pass, no C:\HelixSR left in the launcher. R3-recordhelixsr-untested
      skipped: no harness constructs TemporalUpscaler; the in-game run is its evidence. Review: high risk, due;
      consent prompt relayed in chat (the question tool failed), awaiting the user.
- [x] T4 In-game verification. The user played (2026-10-11, 12:12-12:16): Hunter's Dream renders correctly
      (user screenshot), ~130 FPS by the user's eye, log `Perf:` 120-164 FPS without frame generation, 19,200
      HelixSR dispatches at 1280x720 -> 1920x1080 with no fallback. The session then ended in a device loss right
      after the user switched frame generation on mid-game; the user says that crash happens with every
      upscaler (pre-existing, out of scope here, while README:177 says the in-game switch works). Not done:
      the same-spot comparison with FSR 3.1/DLSS, and the Native AA path (preset 0).
- [x] T5 Docs: README section (install HelixSR, point the launcher at it, limits) and CHANGELOG.
      Route: delegated writer; parent fixed the FAQ title ("any card" overclaimed). Commit 737cac0. Also
      documents the L3+R3 quit box (aab046f) and opens an Unreleased changelog section. Evidence: 457 Python
      tests pass; only README.md and CHANGELOG.md changed.

## Design notes (from the code map, 2026-10-11)

- T1 bridge: `gpu/shadps4/video_core/renderer_vulkan/vk_helixsr.{h,cpp}`, C Vulkan API only (vulkan.hpp's dynamic
  dispatcher changes layout with `VK_USE_PLATFORM_WIN32_KHR`, defined only in `vk_platform.cpp`). D3D12 device on the
  Vulkan device's LUID, one shared D3D12 fence imported as a Vulkan timeline semaphore, shared D3D12 textures imported
  as dedicated Vulkan images, depth copied through a buffer (no depth-to-color image copy in Vulkan). Never calls
  `ID3D12Resource::GetDesc` (MinGW struct-return ABI). Check: `tests/test_helixsr_bridge.cpp`,
  target `helixsr-bridge-test`.
- T2 game side, mirrored on DLSS: new `UpscalerHelixSr` before `UpscalerCount` (`gpu/shim/bbport_settings.h:12`),
  a `RecordHelixSr` branch in both chains (`vk_temporal_upscaler.cpp` Run ~1211, RunScaled ~1815) like `RecordDlss`
  (own context, LDR color with no HDR flag on the scaled path, writes `output_image`). The branch flushes the
  scheduler twice: inputs + signal, then D3D12 dispatch, then output copy + wait. The new wait must be the FIRST wait of
  its SubmitInfo (the sync submit path pairs wait slot 1 with COLOR_ATTACHMENT_OUTPUT, `vk_scheduler.cpp` ~903).
  After the branch the caller must fetch `scheduler.CommandBuffer()` again: the scaled path blits from the
  pre-flush `cmdbuf` (~1954). Jitter and render size stay the port's own (Halton 2,3; `SceneResolution::ForPreset`).
  Device extensions `VK_KHR_external_memory_win32` + `VK_KHR_external_semaphore_win32` requested only when the DLL
  is present (pattern: fsr4vk, `vk_instance.cpp` ~452), plus a LUID getter from `vk11_props`. Failure: log,
  `helixsr_failed = true`, upscaler back to FSR 3.1, reset.
- T3 launcher (`launcher/bbport_launcher_win.py`), mirrored on FSR 4.1.1 (fsr4vk): listed as experimental, needs the
  Experimental switch; `BB_HELIXSR_DIR` names the folder holding HelixSR's built `amd_fidelityfx_dx12.dll` (built
  means `helixsr_setup.json` sits next to it); `BB_HELIXSR=0` unless Experimental. GPU verdict row `helixsr` in
  `tools/gpu_capabilities.c` (`--upscalers`): both Win32 external extensions present.

## Acceptance criteria

- With HelixSR selected and its DLL present, the game renders upscaled frames from HelixSR (helixsr.log shows the
  network running, no red border) at a stable frame rate.
- With the DLL missing or the bridge failing, the menu says why and the game runs on FSR 3.1.
- Other upscalers behave exactly as before.

## Checks

- Standalone check program: exit 0 with each DLL, output image non-black and close to the input gradient.
- Build: `ninja -C out/gpu bb-probe bb-play`.
- Tests: `python -m pytest tests -q` (Windows subset).
- In-game: screenshots and the Perf line from the log.

## Progress

- 2026-10-11: HelixSR 1.8.1 downloaded to `C:\Users\WinterOS\HelixSR`, network built with its own setup
  (60 shaders, 7.8 MB, no game touched). Both `amd_fidelityfx_dx12.dll` (HelixSR) and
  `amd-fsr/amd_fidelityfx_upscaler_dx12.dll` (AMD 4.1.1) export ffxCreateContext/Destroy/Configure/Query/Dispatch.
  Branch `feat/helixsr` created from develop at aab046f.
- Engram mirror (`odd/helixsr-vulkan-bridge/tasks`): pending, the save hook could not confirm the session
  registration on 2026-10-11.

## Next step

Map the existing upscaler wiring (explorer), then write T1's spec and hand it to one writer.
