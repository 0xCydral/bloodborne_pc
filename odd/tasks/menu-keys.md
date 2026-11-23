# Menu keys: both overlays open on a keyboard without Home or Insert

Objective: every player can open Luma's overlay (ReShade, on the injected route) and OptiScaler's menu (HelixSR
Scaler), and the injected route runs DLSS without opening anything at all. Laptops often have no Home key (ReShade's
default overlay key) and no Insert key (OptiScaler's default menu key), so today those players cannot reach either
menu - and the injected route's DLSS call is chosen in Luma's overlay.

Why: ReShade's overlay key and OptiScaler's menu key are plain ini values, and Luma UE's upscaler choice is one too:

- `ReShade.ini` next to the game's .exe, `[INPUT] KeyOverlay` (default `36,0,0,0` = Home): a ReShade 6.8 build
  loaded as `ReShade64.dll` (not a proxy name) defaults its base path to the target executable's folder
  (reshade dll_main.cpp `get_base_path`, `is_dxgi`/`is_d3d` false for `ReShade64`), and the effect runtime's config
  is `<base path>/ReShade.ini` (runtime_manager.cpp `create_effect_runtime`, first runtime named "ReShade").
  `KeyOverlay` is an array of four ints: virtual key, ctrl, shift, alt (runtime_gui.cpp `config_get("INPUT",
  "KeyOverlay", _overlay_key_data)`, `input::key_name`); with the default `ForceShortcutModifiers=true` the modifier
  state must match exactly (runtime.hpp, input.cpp). A file written before the first run is the config ReShade reads
  (ini_file::load_cache reads it at startup).
- `OptiScaler.ini` `[Menu] ShortcutKey`: one virtual-key code, no modifiers (OptiScaler v0.9.4-final 7534ad0,
  Config.h `CustomOptional<int> ShortcutKey { VK_INSERT }`, the shipped ini's own comment "Default (auto) is 0x2D
  which equals VK_INSERT; previous default key was 0x24 -> Home"; readInt accepts `0x2d` hex or decimal, writes hex;
  the menu compares the raw key code, so no key combination is possible). HelixSR Scaler 1.0.3 ships OptiScaler
  v0.9.4-final (7534ad0) - the fork's own ini in the 1.9.0 release says the same.
- Luma UE keeps its settings through ReShade's config API under the section `Luma` (LumaFramework `Globals::MOD_NAME`
  = "Luma"; `get_config_value(runtime, NAME, "SRUserType", ...)` in `load_config`, `set_config_value` in the Super
  Resolution combo). `SRUserType`: None 0, Auto 1, DLSS 2, FSR 3 3 (source `super_resolution.h`). A missing key
  reads as Auto, which is why the mod's own wiki tells the user to open the overlay. Writing `SRUserType=2` makes a
  fresh install run the DLSS call HelixSR executes with nothing to open. Both the global config (Luma reads with
  `runtime=nullptr`) and the runtime config resolve to the same `ReShade.ini` in the game's .exe folder.

Decisions:

- OptiScaler's menu key becomes **F8** (`0x77`): OptiScaler has no modifier support, so it must be a single key every
  keyboard has; F8 is a function key (present on laptops, unlike Insert/Home), is not used by Steam (F12 is the
  screenshot key), by OptiScaler itself (PageUp/PageDown/End) and by very few games. Insert stays the ini's own
  default, so a player who set a key by hand keeps it.
- ReShade's overlay key becomes **Ctrl+Shift+F7** (`118,1,1,0`): ReShade does support modifiers; a combination keeps
  game bindings from triggering it, and F7 is on every keyboard. A different base key than F8 keeps the two menus
  from opening together. F12, F5/F9 (save/load), F1 (help) and F11 (fullscreen toggles) are avoided.
- Both writes are made only where the ini still holds its own default (`auto`, Home) or has no line at all: a value
  the player set is never overwritten. What was there is recorded (`framegen.json`, the existing record) and Remove
  puts it back (a line AMDLSS added is dropped again).
- The `ReShade.ini` the injected route creates is added to the Luma record's `created` list with its SHA-256, so
  Remove deletes it while it is untouched and keeps (and reports) it once ReShade or the player changed it.
- No settings picker: a fixed default works, and the ini stays hand-editable.

Scope: `amdlss/framegen.py` (a "write only where the ini still holds its own default" apply, `Setting.ini`, the
menu key constant, `forget` for a second ini), `amdlss/scaler.py` (write the menu key after an install),
`amdlss/luma.py` (DLSS preset, overlay key, record entry, texts), `amdlss/games.py` and `amdlss/fsr4.py` (texts),
`amdlss/selftest.py`, tests, `README.md`. Nothing is pushed, tagged or released here.

## Tasks

- [ ] framegen: `apply_default` (ours only where the ini holds its default or nothing, record and put back; a line
      we added is dropped), `Setting.ini`, `MENU_KEY`; `forget` drops every record for the game folder's inis
- [ ] scaler: apply `MENU_KEY` after an install (both routes go through `scaler.install_game`)
- [ ] luma: `[Luma] SRUserType=2` and `[INPUT] KeyOverlay=118,1,1,0` through `apply_default`, recorded, restored on
      Remove; the created ReShade.ini enters the Luma record
- [ ] texts: window, logs and README name the keys AMDLSS sets instead of Home/Insert
- [ ] tests: framegen on/off/implicit cases, luma install/remove of ReShade.ini, a player's own key untouched

## Checks

- `setsid Xvfb :66 -screen 0 1280x900x24 &` then `DISPLAY=:66 uv run --no-project -q --managed-python --python 3.12
  --with pytest --with pillow python -m pytest -q`, `node --test receiver/`, `python AMDLSS.pyw --selftest`.
- Not verified: a real game with Luma UE and OptiScaler (the preset, the two keys and ReShade's rewrite of its own
  ini can only be observed under Windows with the game running).

## Progress and evidence

- Research sources read: crosire/reshade main (`runtime.cpp`, `runtime_gui.cpp`, `runtime_manager.cpp`,
  `dll_main.cpp`, `input.cpp`, `ini_file.cpp`), optiscaler/OptiScaler `7534ad0` (`Config.h`, `Config.cpp`) and the
  Scaler 1.0.3/HelixSR 1.9.0 ini and DLL ("OptiScaler v0.9.4-final (7534ad0)"), Filoppi/Luma-Framework
  (`core.hpp`, `super_resolution.h`, `globals.h`, `Source/Games/Unreal Engine/main.cpp`) and the shipped ReShade
  6.8 build's own strings (`KeyOverlay`, `KeyEffects`, `ReShade.ini`, "6.8.0 UNOFFICIAL") plus Luma's addon strings
  (`SRUserType`, `FirstBoot`).
- The public ReShade source read matches the shipped build for the keys used here (`INPUT KeyOverlay` in
  runtime_gui.cpp; the `KeyEffects` name is a different shortcut, the effect toggle).
- Not yet observed: the tests below.
