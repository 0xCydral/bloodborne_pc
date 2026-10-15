# AMDLSS 1.1 HelixSR parity: the re-uploaded asset, a copy's zip record, the setup log, and DLSS 4 frame generation

## Objective

Bring the port's HelixSR launcher up to the parity of the HelixSR work AMDLSS 1.1.1 shipped (items 1-4 of the
brief, plus the smaller lessons of item 6), the way the earlier AMDLSS features were ported (82c08f1, 2edbf93,
0270f34). Item 5 (DLSS 4 frame generation through HelixSR) is assessed here, not implemented.

## Why, item by item

1. **The pinned asset is dead.** The port pinned `HelixSR-1.9.0.2.zip` (2edbf93); the release now serves
   `HelixSR-1.9.0.zip` and the old name 404s, so **Install HelixSR** and **Reinstall** failed. This was the
   urgent fix.
2. **A build must come from the pinned zip.** A folder built from another zip has the same folder and version
   name as the current one, so it passed for current: no update was offered and the current zip's files were
   never extracted over it. AMDLSS records which zip was extracted and treats any other folder as outdated;
   the port now does the same, with **Update HelixSR**.
3. **The setup's output must survive the window.** The launcher's log box is gone with the launcher; the whole
   run is now appended to `helixsr\helixsr-setup.log`, so a failed build stays diagnosable.
4. **`amd-fsr` is not needed.** Checked: the port references no `amd-fsr` folder anywhere (repo-wide grep);
   FSR 4.1.1 comes from the fsr4vk provider (`tools/fetch_fsr4vk.py`, `gpu/.../vk_fsr4vk.cpp`), and HelixSR is
   only the FSR 3.1 drop-in DLL (`gpu/.../vk_helixsr.cpp` checks `amd_fidelityfx_dx12.dll` +
   `helixsr_setup.json`). The HelixSR Scaler package (where AMD's FSR 4 DLL moved in 1.9.0) is not needed, and
   the port has no OptiScaler route at all. **Skipped as not applicable.**
5. **DLSS 4 frame generation**: assessed below. **No renderer work done**, as instructed.
6. **Smaller lessons**, checked one by one:
   - *Log the success path of an install*: ported — the success note names the folder that was built
     (`helixsr_install_done`, launcher:3882) and the setup log keeps the run's own final lines.
   - *Never claim FG/feature state the files don't back*: the port makes no HelixSR-frame-generation claim (the
     HelixSR bridge doc keeps FG out of scope; the launcher's HelixSR texts name upscaling only), and the new
     Update offer is backed by the zip record, not by a name. Nothing to change.
   - *Frame generation off must be written explicitly*: that lesson fixes AMDLSS's OptiScaler.ini, whose
     `FrameGeneration=auto` turned FG on by itself. The port writes only its own `bbport.ini`, where
     `frame_generation` defaults to `'0'` (launcher:254) and no third-party "auto" exists. Nothing to change.

## Evidence (HelixSR v1.9.0, verified locally 2026-10-15)

- `HelixSR-1.9.0.zip` 3803249 bytes, SHA-256
  `795268f60a1262cd7c8cb01a38f553751cc380fa0cbec63c8f466b6807468be4` (`SHA256SUMS.txt` of the release; the
  downloaded asset was hashed again here).
- `.../download/v1.9.0/HelixSR-1.9.0.2.zip` answers 404; `HelixSR-1.9.0.zip` answers 200 with
  `content-length: 3803249`.
- The zip holds 99 members: `HelixSR-1.9.0/helixsr-setup.ps1` (the script the launcher runs; `-Yes` is still a
  switch), `helixsr-setup.bat`, `amd_fidelityfx_dx12.dll`, `helixsr-fg/nvexec64.dll`, and the `setup/` build
  machinery (`setup/helixsr_setup.py`, `setup/helixsr_install.py`, `setup/fg/*`). The port's `extracted()`
  check (SETUP + DLL) and `setup_command()` (`helixsr-setup.ps1 -Yes`) stay valid.
- The setup's own flow (its plain-text scripts): a `--status` pass reports `network=`/`fg=`; it builds only
  what is missing; the network is embedded into the DLL and `helixsr_setup.json` is written; frame generation
  downloads NVIDIA's Streamline 2.8.0 `nvngx_dlssg.dll`, `_nvngx.dll` out of NVIDIA's 595.71.05 driver package
  (~404 MB) and Intel's `libxell.dll`, and embeds its network into the DLL too (footer `HXSRFG01`) with an
  `helixsr-fg\` folder (about 400 MB of NVIDIA downloads once). The per-game step runs `helixsr_install.py`;
  with stdin closed its questions hit EOF, `ask()` returns False and `main()` returns, exit 0 (its
  :1211/:1338) — the port's no-window contract holds.
- The port's one-click runs the setup unmodified with `-Yes`, so it now also builds the frame generation; the
  consent text (launcher:503) and README name the downloads (about 4 MB zip, ~70 MB tools, ~400 MB NVIDIA for
  FG) instead of the old "about 57 MB".

## Scope

`tools/fetch_helixsr.py`, `launcher/bbport_launcher_win.py`, `tests/test_fetch_helixsr.py`,
`tests/test_launcher_helixsr.py`, `README.md`, `CHANGELOG.md`. No C++ (`gpu/`) change; no packaging change
(the helper was already shipped); no push, no release, no tag.

## Decisions

- **Keep the setup invocation as it is (no `-NoFg`).** The port has always run HelixSR's own setup, unmodified,
  with `-Yes`; 1.9.0's setup builds the upscaler network *and* its frame generation, and the consent text now
  discloses the ~400 MB that costs. `-NoFg` would save that download and is the right call if the port decides
  it will never use HelixSR's frame generation; it is not done here because item 5 is an assessment only and
  the reference (AMDLSS) deliberately keeps the files built. The FG build's failure is non-fatal in the setup
  (a step line, exit 0), so the port's success criterion (`helixsr_setup.json`) is unaffected.
- **The zip record is `helixsr-package.json` beside `HelixSR-1.9.0\`** (fields `version`, `zip_name`,
  `zip_sha256`, `folder`), written by `fetch()` the moment the verified zip is on disk — the same shape as
  AMDLSS. `fetch()` also drops the replaced build's record (`forget_build`), so a failed rebuild of the new
  files cannot be passed by the old `helixsr_setup.json`.
- **`build()` keeps a record the setup did not rewrite**: 1.9.0's setup builds only what is missing, so a run
  whose network is built does not write `helixsr_setup.json` again; unlinking it first made a re-run (and the
  command line) fail on "helixsr_setup.json is missing" (tools/fetch_helixsr.py:233).
- **Only the launcher's own copy is considered for Update** (`helixsr_outdated`, launcher:532): the setting is
  empty and the found folder sits directly under `<port>\helixsr`. A folder the player chose is theirs; a copy
  *newer* than the pinned release is left alone; an older one, or one whose record does not match the pinned
  zip, shows **Update HelixSR** (and the press refetches instead of building in place, launcher:3839).
- **The setup log is `helixsr-setup.log` in the install target** (`<port>\helixsr`), appended with a
  timestamped heading per run; a log that cannot be written never fails a build (`SetupLog`,
  tools/fetch_helixsr.py:199). The port has no log collector, so the README's bug-report line now names the
  file (the launcher's log box is not a file).

## Tasks

- [x] T1 Repin the asset (`HelixSR-1.9.0.zip`, 3803249, `795268f6…`; tools/fetch_helixsr.py:38-44), update the
  pin test and the consent text; README/CHANGELOG. Commit `4239951`.
- [x] T2 Zip record + `package_current` + `forget_build` (tools/fetch_helixsr.py:144-168), `build()` keeps the
  record (:233), the launcher's **Update HelixSR** offer + force (:3804-3824, :3836-3839) and the success note
  (:3877-3883). Commit `4239951`.
- [x] T3 Setup log (`SETUP_LOG`, `SetupLog`, `install()` passes the path; tools/fetch_helixsr.py:49,199-224,
  :268) + README's bug-report line. Commit `4239951`.
- [x] T4 Item 4 and the two inapplicable item-6 lessons checked and recorded above.
- [x] T5 DLSS 4 FG feasibility assessed below; no renderer code.

## Evidence (observed)

- `python3 -m unittest discover -s tests` → `Ran 553 tests ... FAILED (failures=1, skipped=284)`. The one
  failure is `test_launcher_controls.TranslationTests.test_the_file_keeps_its_windows_line_endings`, which
  expects a CRLF checkout of `launcher/bbport_lang.py`; that file is LF both in this checkout and at the base
  commit (bytes unchanged by this work) — pre-existing and environment-dependent, not part of this change.
- `python3 -m unittest discover -s tests -p "test_fetch_helixsr.py"` → `Ran 25 tests ... OK`.
- The launcher tests are `skipUnless(sys.platform == 'win32')`; they skip on this Linux box and run in CI
  (`.github/workflows/windows-package.yml` runs pytest on windows-latest). On Linux the same states were
  smoke-run by importing the launcher directly: `helixsr_outdated` true for a stale/older/record-less copy,
  false for pinned+record, newer, chosen, outside, and helper-missing; `helixsr_folder` still picks the
  newest; the consent text names the license, 4 MB, ~400 MB and ~70 MB in both languages.
- Live fetch with the real pins (`fetch()` into a temp dir) → `HelixSR-1.9.0` extracted, `package_current`
  true, record `{'version': '1.9.0', 'zip_name': 'HelixSR-1.9.0.zip', 'zip_sha256': '795268f6…', 'folder':
  'HelixSR-1.9.0'}`, `helixsr-setup.ps1` and the DLL present.
- The Windows-side setup itself was not run here (no PowerShell on Linux) and the game was not started.

## DLSS 4 frame generation through HelixSR (assessment, no code)

What HelixSR ships for it:

- HelixSR 1.9.0's setup translates NVIDIA's DLSS Frame Generation network and packs it into
  `amd_fidelityfx_dx12.dll` (D3D12 compute shaders, footer `HXSRFG01`), and builds `helixsr-fg\`:
  `nvexec64.dll` (executor; exports `nvexec_fg_create/evaluate/evaluate7/8/9/destroy/measure`,
  `nvexec_hook_device`, `nvapi_QueryInterface`), `nvngx_dlssg.dll` (NVIDIA's Streamline 2.8.0 module; exports
  `NVSDK_NGX_VULKAN_*` plus D3D11/D3D12/CUDA entry points), `_nvngx.dll` (NVIDIA's NGX core from driver
  595.71.05) and `libxell.dll` (Intel XeLL for low latency). Verified with `objdump -p` on the shipped/built
  files and the setup's own sources; the package also carries Vulkan kernels under `setup/fg/data/vk/`
  (`*.vk.spv`, a relay and a reset blob), though which path the modules select was not verified.
- The HelixSR DLL itself exports the FSR 3.1 drop-in API (`ffxCreateContext` …) and the NGX **D3D12** API
  (`NVSDK_NGX_D3D12_*`); the NGX **Vulkan** entry points live in the `nvngx_dlssg.dll` HelixSR's setup builds.
  A Vulkan host (this port) would therefore drive the FG through NVIDIA's NGX Vulkan API against the modules
  in the player's own `helixsr-fg\`.

What the bridge would have to call (in the shape of today's `vk_helixsr` bridge):

- `NVSDK_NGX_VULKAN_Init`/`Shutdown1` with the port's `VkInstance`/`VkDevice` and the NGX parameters object,
  `GetFeatureRequirements`/`GetScratchBufferSize`, `CreateFeature1`/`ReleaseFeature` for the frame-generation
  feature, and `EvaluateFeature` once per presented frame, with a command buffer.
- Its inputs are the same set the port already produces for its upscalers and its own frame generation: color,
  depth, motion vectors, jitter, exposure, frame time and the render/output sizes (`Vulkan::HelixSr::FrameParams`,
  `vk_helixsr.h`); DLSS-G additionally wants the presented image as its output and a reset signal on
  resize/loading.
- The present path is the port's own (`vk_frame_generation.cpp` interleaves its FSR 3.1 generated frames), so
  the seam exists, but DLSS-G's own present-order contract (evaluate between the real frame and its present,
  swapchain images, UI handling) would have to be mapped onto it.

Effort estimate: a prototype is a multi-week spike (2-4 weeks of focused work), not a small port. The big
unknowns: the NGX Vulkan contract on a non-NVIDIA GPU (HelixSR's own shims are what make that work for
HelixSR Scaler; the port would consume the same built files, but nothing here verifies it), the present
ordering/timing with the port's swapchain and its existing frame-generation seam, and validating without a
Windows test game. The files the setup now places are the prerequisite and are covered by the install
disclosure above.

## Unverified / limits

- The Windows launcher tests and CI were not run from here; they skip on Linux.
- PowerShell's setup was not executed here (the pins, the zip layout and the scripts' behavior were read and
  hashed instead; the live fetch of the pinned zip was run).
- No in-game run of HelixSR 1.9.0 in the port (unchanged from 2edbf93's state).
