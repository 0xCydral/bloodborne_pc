# HelixSR 1.9.1: the 137 MB release, the frame-generation question, and what the port reads from it

## Objective

Move the port's HelixSR pin from 1.9.0 (`HelixSR-1.9.0.zip`, 3803249 B) to 1.9.1
(`HelixSR-1.9.1.zip`, 137494566 B), after diffing both release zips side by side: the member list, the setup
entry points and their flags (`-NoFg` in particular), every file/path/export the port reads from the folder,
and the new download size. Keep frame generation skipped in the one-click install, keep a 1.9.0 build offered as
**Update HelixSR**, and touch C++ only if the diff demands it.

## The diff (both zips in their own empty dirs; hashes checked against the release)

- 1.9.0 zip: 3803249 B, SHA-256 `795268f60a1262cd7c8cb01a38f553751cc380fa0cbec63c8f466b6807468be4` — the current
  pin, re-verified here (downloaded again).
- 1.9.1 zip: 137494566 B, SHA-256 `9f329a8a4359bc21830b1e0b3f8211f52d851760faddc5cbde74e245cf5140ff` — the value in
  the release's `SHA256SUMS.txt`, fetched with it; the asset matches byte for byte.
- Members: 99 → 130, all under the version folder (`HelixSR-1.9.0/` → `HelixSR-1.9.1/`).
- **Added** (32, all one subtree): `helixsr-scaler/` — HelixSR Scaler 1.0.4: `OptiScaler.dll`, `OptiScaler.ini`,
  `OptiScaler/D3D12_OptiScaler/D3D12Core.dll`, `amd_fidelityfx_{framegeneration,loader,upscaler,kernels}*_dx12.dll`,
  `amd_fidelityfx_vk.dll`, `libxess*.dll`, `libxell.dll`, `streamline/*.dll`, licenses,
  `HelixSR-Scaler-package.txt`, `README.txt`, `setup_windows.bat`, `setup_linux.sh`. About 290 MB unpacked —
  the reason the zip grew from 3.8 MB to 137 MB. The port never reads this folder.
- **Removed**: `setup/__pycache__/helixsr_install.cpython-314.pyc` (a build artifact that had shipped).
- **Changed content** (12 of the 98 common files):
  - `amd_fidelityfx_dx12.dll` 3398656 → 4254720 B (the runtime the port loads);
  - `helixsr-fg/nvexec64.dll` 614912 → 617472 B;
  - `helixsr-setup.ps1`, `helixsr-setup.bat`, `helixsr-setup.sh`, `helixsr-uninstall.ps1`, `helixsr-uninstall.sh`,
    `setup/helixsr_setup.py`, `setup/helixsr_install.py`, `README.md`, `SOURCE.md`, `THIRD_PARTY_NOTICES.md`.
- **Unchanged**: every `setup/fg/data/vk/*` FG kernel byte, the shipped network-less DLL layout, the folder name
  scheme, and the names the port reads.

## Setup entry points and flags (1.9.1, read only)

- `helixsr-setup.bat` still calls
  `powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0helixsr-setup.ps1" %*` (:2).
- `helixsr-setup.ps1` still declares `-NoFg` (:25) and still forwards it as `helixsr_setup.py --no-fg` (:125).
  New: a `-Fg` switch (:26) and a one-time question whose "no" is kept in `helixsr_fg_choice.txt` (:79-104) —
  asked only when `-NoFg` was not passed and the folder has no frame generation yet (:83). With `-NoFg`:
  `$skipFg = $true` (:81), the question at :94 is never reached, `$fg` is forced true (:104), so an already-built
  folder ends in "the network is already built" (:106) and a fresh one builds exactly as before.
- `setup/helixsr_setup.py`: `build_fg()` still returns at once on `--no-fg` (:247); `--status` still prints
  `network=`/`fg=` (:265-267); `helixsr_setup.json` is still written (with a new `fp32` field) (:434). New:
  `w64_fp32` kernel variants for GCN without 16-bit types (:338, :367-368) — inside the DLL, picked per GPU.
- The per-game step (`setup/helixsr_install.py`) now also installs HelixSR Scaler for DLSS/XeSS/FSR 2 games
  (:52) and fills a `manual/` folder (:1659, :1729). With stdin closed every question EOFs out
  (`ask()` :1830-1831, the input loops break) and `main()` returns — exit 0, the port's no-window contract.
- `network_ready()` (:270) is true after a `-NoFg` build (the network is embedded in the DLL), so the game part
  that runs after the build passes.

## What the port reads from the folder (all unchanged in 1.9.1)

- `gpu/shadps4/video_core/renderer_vulkan/vk_helixsr.cpp` loads `amd_fidelityfx_dx12.dll` (:346) and resolves
  `ffxCreateContext`, `ffxDestroyContext`, `ffxConfigure`, `ffxQuery`, `ffxDispatch` (:350-356); `Problem()`
  requires `helixsr_setup.json` beside it (:1081-1089). `objdump -p` shows the new DLL exports the **same 22
  names** (diff empty) — including all five. The bridge never loads a model file or a `setup/fg` kernel: the
  network is embedded into the DLL by the setup, so the recalibrated int8 weights and the GCN shaders arrive
  with a normal rebuild. **No C++ change.**
- `launcher/bbport_launcher_win.py`: `HELIXSR_DLL`/`HELIXSR_BUILT` (:469-470), the folder and outdated rules
  (:487-540); `tests/test_launcher_helixsr.py` exercises them.
- `tools/fetch_helixsr.py`: `SETUP = 'helixsr-setup.ps1'` (the `extracted()` gate), `DLL`, `BUILT`, `PACKAGE`.

## What breaks for the port

Nothing in the load path. The pin's size/SHA change, and the docs that name the download size (the consent text
says "about 4 MB") and the by-hand FAQ path (1.9.1's `.bat` asks about frame generation first; answering `n` is
what this port wants, since it uses HelixSR as an upscaler only). The pin tests move to 1.9.1, and the launcher
tests get a 1.9.0 folder as the **Update HelixSR** case.

## Decisions

- Keep `setup_command()`'s `-Yes -NoFg`; re-verified in 1.9.1: the switch, its `--no-fg` mapping and the early
  return in `build_fg()` all still exist, and `-NoFg` keeps the new question from ever being asked.
- State the new download size as "about 131 MB" (`137494566 >> 20`), the same MiB style AMDLSS uses for the
  same release family.
- No `gpu/` change, no packaging change, no push, no release.

## Tasks

- [x] T1 Diff the two zips (member lists, changed content, setup flags, DLL exports, port read paths) — evidence
  above, zips downloaded to `/tmp/helixsr-diff/download-1.9.{0,1}/`, read with plain tools only.
- [x] T2 Tests first: pin test to 1.9.1; launcher tests into the pinned copy / other zip / 1.9.0-as-older cases.
  The pin test failed against the old source on purpose (`Ran 25 tests ... FAILED (failures=1)`) and passes after
  T3.
- [x] T3 Source: repin `tools/fetch_helixsr.py` (VERSION/ZIP_NAME/ZIP_SIZE/ZIP_SHA256, docstrings); consent text
  EN+RU (about 131 MB); README (version, size, the by-hand frame-generation answer, the GCN note); CHANGELOG
  `[Unreleased]`.
- [x] T4 Run `python3 -m unittest discover -s tests` → `Ran 553 tests in 31.6s` / `OK (skipped=284)`; smoke the
  launcher rules by direct import (they skip on Linux) — evidence below.
- [x] T5 Commit (conventional, exact-path staging; no push): `feat(helixsr): install HelixSR 1.9.1` and this
  `docs(tasks)` commit.

## Evidence (observed)

- `python3 -m unittest discover -s tests -p "test_fetch_helixsr.py"`: `FAILED (failures=1)` on the pin test
  before the repin (the new test against the old source), `Ran 25 tests ... OK` after.
- `python3 -m unittest discover -s tests`: `Ran 553 tests in 31.639s` → `OK (skipped=284)` (same count as before
  the bump; the Windows launcher tests skip on this Linux box and run in CI).
- Launcher rules smoke-run by importing the launcher directly (throwaway `/tmp/helixsr-smoke.py`, all PASS):
  pinned 1.9.1 + its record current; a 1.9.0 build and a 1.9.1 copy from another zip both offered as Update;
  a newer 1.10.0 copy, a chosen folder and a folder outside `<port>\helixsr` left alone; `helixsr_folder` picks
  the newest; the consent text names 131 MB, the version and NVIDIA's license and says no "400" — EN and RU.
- Live fetch with the real pins (`fetch()` into a temp dir): `HelixSR-1.9.1` extracted, `package_current` true,
  record `{'version': '1.9.1', 'zip_name': 'HelixSR-1.9.1.zip', 'zip_sha256': '9f329a8a…', 'folder':
  'HelixSR-1.9.1'}`, `helixsr-setup.ps1` and the DLL (4254720 bytes) present, `helixsr-scaler/` (36 entries)
  extracted, `built()` false until HelixSR's setup runs.
- The Windows setup itself was not run (no PowerShell here): the setup chain was read from both zips, not
  executed; the launcher window and the game were not exercised.
