# HelixSR 2.0.0: the DLL moves to HelixSR-fsrfg, and `-Yes` now installs into games

## Objective

Move the port's HelixSR pin from 1.9.1 (`HelixSR-1.9.1.zip`, 137494566 B) to 2.0.0 (`HelixSR-2.0.0.zip`,
136997025 B), after diffing the releases: the layout, the setup entry points and flags, the DLL exports, and every
file or path the port reads. Keep frame generation skipped in the one-click install, keep a 1.9.x folder working
as the **Update HelixSR** case.

## The diff

- **1.9.1 cannot be downloaded any more.** `gh release list -R lonewolf0622/HelixSR` has v1.9.0, v1.9.2
  (pre-release), v1.9.3 and v2.0.0; `.../download/v1.9.1/HelixSR-1.9.1.zip` answers 404 (2026-10-19). The current
  pin of the port is a dead link. The diff below is therefore 1.9.3 (closest release, the last 1.9.x) against 2.0.0;
  the 1.9.1 facts are the ones recorded in `odd/tasks/helixsr-1.9.1.md`.
- 2.0.0 zip: 136997025 B, SHA-256 `0d60686eddbb0abd680b27d12a088344b212464ebf0a2dc18d10387e1c817f52`, equal to the
  release's `SHA256SUMS.txt` and to a fresh download. 149 files, about 222 MB unpacked, all under `HelixSR-2.0.0/`.
  1.9.3 zip (SHA-256 `7094b7af...a1d1`, local): 84 files.
- **Layout change (the breaking one).** `amd_fidelityfx_dx12.dll` (4254720 B in 1.9.1, now 8.0 MB), a new
  `helixsr.ini`, and `helixsr-fg/nvexec64.dll` live in `HelixSR-2.0.0/HelixSR-fsrfg/`; the release root no longer
  has the DLL. `helixsr_setup.json` is still written at the release root (`setup/helixsr_setup.py:465`, `rel`),
  one level above the DLL (`FSRFG`, `release_dirs()` :260-267).
- **Added**: `HelixSR-fsrfg/`, `helixsr-scaler/README-Linux.txt`, `setup/helixsr_bridge.py`,
  `setup/helixsr_games.py`, `setup/fg/**` (frame-generation setup, kernels, data), `setup/lib/k5_xform.py`,
  `setup/lib/setup_shader_jobs.py`, runtime licenses. **Removed**: the root DLL, `helixsr-desktop-fg.txt`, the
  Streamline DLLs under `helixsr-scaler/OptiScaler/streamline/`. **Changed**: the three setup scripts, uninstall
  scripts, `setup/helixsr_setup.py`, `setup/helixsr_install.py`, `OptiScaler.dll`/`.ini`, README, SOURCE.
- **DLL exports** (`objdump -p`): the 22 names of 1.9.3 plus four (`HelixSR_FfxFgDlssgVersion`,
  `HelixSR_FfxFgUiFlag`, `HelixSR_FgPresenterGetApi`, `HelixSR_NgxFgVersion`). `ffxCreateContext`,
  `ffxDestroyContext`, `ffxConfigure`, `ffxQuery` and `ffxDispatch` are all still exported.

## Setup entry points and flags (2.0.0, read only)

- `helixsr-setup.bat` still forwards `%*` to `helixsr-setup.ps1`. `-NoFg` (:16) and `-Yes` (:17) still exist.
  `-NoFg` forces `$fg` true (:70) and passes `--no-fg` (:90); `build_fg()` returns 0 at once on it
  (`helixsr_setup.py:300`); `--status` still prints `network=`/`fg=` (:347). The question about frame generation of
  1.9.x is gone: 2.0.0 builds it by default, so `-NoFg` is what keeps the about 400 MB of NVIDIA files away.
- **`-Yes` changed meaning (the trap).** :113 `if ($Yes) { $inst += '--yes' }` hands it to `helixsr_install.py`,
  whose `ASSUME_YES` makes `answer()` return each question's default: the first way shown, so "install"
  for every game found in Steam, Epic, GOG, Xbox, Heroic and Lutris. 1.9.x did not pass it on. The port's contract
  ("nothing is installed into other games") would break with the old command line.
- The only question the build itself asks is the DLSS download: `download_dlss()` (`helixsr_setup.py:37`),
  `input('Download it now? [Y/n] ')`, skipped by `--yes`.

## What the port reads from the folder

- `gpu/shadps4/video_core/renderer_vulkan/vk_helixsr.cpp`: `DllPath()` and the loader use
  `BB_HELIXSR_DIR\amd_fidelityfx_dx12.dll`; `Problem()` required `helixsr_setup.json` beside it, which fails for
  2.0.0 whatever the launcher does. Fixed to accept the file beside the DLL or one folder above.
- `launcher/bbport_launcher_win.py`: `helixsr_folder()` (the folder that holds the DLL, now also
  `<release>\HelixSR-fsrfg`), `helixsr_built()`, `helixsr_state()`, `helixsr_outdated()` (compares the release
  folder, not the DLL folder).
- `tools/fetch_helixsr.py`: `extracted()`, `built()`, the layout error, `setup_command()`, `build()`.

## Decisions

- Resolve the DLL folder in one place per language (`dll_folder` in the fetch tool, `helixsr_dll_folder` in the
  launcher); the launcher keeps `BB_HELIXSR_DIR` = the DLL folder, so the game's loader line did not change.
- Accept the record beside the DLL **or** one folder above in the bridge, the launcher and the fetch tool: a 1.9.x
  folder (record beside the DLL) keeps working and shows as **Update HelixSR**; 2.0.0 (record one up) works.
- No `-Yes`. The launcher's consent dialog stays the player's agreement; `build()` answers the one DLSS-download
  question with `y\n` on the setup's stdin and closes it. `y` is not a valid answer to any per-game question
  (`s/f/r/n`), so if the download question is not asked (network already built) the installer reads `y`, rejects it,
  reads EOF and changes no game. The old `DEVNULL` would make the download question fail with EOF.
- State the size as "about 131 MB" (136997025 B = 130.7 MiB, rounded), unchanged from 1.9.1.

## Tasks

- [x] T1 Diff the releases (layout, entry points and flags, exports, port read paths).
- [x] T2 Tests first: pin, layout and stdin tests; the launcher tests went red against the old launcher (9
  failures) and green after.
- [x] T3 Source: fetch tool, launcher, bridge `Problem()`; README; CHANGELOG `[Unreleased]`.
- [x] T4 Run the suite; commit.

## Evidence (observed)

- `python3 -m unittest discover -s tests -p "test_fetch_helixsr.py"`: `Ran 32 tests ... OK`.
- Launcher tests skip on Linux (`OK (skipped=70)`); run here by importing the launcher and claiming `win32`
  (throwaway `/tmp/hx-diff/run_launcher_tests.py`): `Ran 70 tests ... OK`, and 9 failures with the old launcher.
- `python3 -m unittest discover -s tests`: `Ran 573 tests in 33.8s` / `OK (skipped=293)`.
- C++: the three-line `Problem()` change was compiled standalone with `g++ -std=c++20 -Wall` and run on a
  temporary tree (no record: unbuilt; record one folder up: ok; flat record: ok). The Windows renderer was not
  built.
- Not run: PowerShell and the setup itself (none here); the stdin answer reaching the setup's embedded Python
  through `powershell -File` is read from the scripts, not observed.
