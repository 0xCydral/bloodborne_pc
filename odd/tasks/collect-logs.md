# Collect logs

**Objective:** one button in the launcher that gives a tester a zip to send after a crash.

**Why:** a tester on an RTX 3060 reported crashes every 60 to 90 minutes (2026-10-26). The launcher had no
way to gather logs, `last_run.log` is overwritten on the next start, and the Windows crash record (faulting
module) was never collected.

**Scope:** the launcher's Log page (Advanced view). No change to run.py or the game.

## Tasks

- [x] T1 `collect_logs()`: zip of `last_run.log`, `previous_run.log`, `bbport.ini`, `settings.json` and
  `system.txt` (launcher version, Windows, CPU, RAM, GPUs with drivers, Application Error / WER / Application
  Hang events of bb-probe, BLauncher and Bloodborne, display driver resets 4101/13/14/153, last 7 days).
  Route: inline (one file).
- [x] T2 `start_game()` keeps the run before as `previous_run.log`.
- [x] T3 **Collect logs** button: off the window's thread, the result line in the log, the zip selected in Explorer.
- [x] T4 Texts in all 11 translation tables; README tip and bug report template point to the zip.
- [x] T5 Release 1.8.0-beta.8.

## Verification

- `tests/test_launcher_logs.py` red first (5 failed: no `collect_logs`, no `previous_run.log`), then 7 passed and
  1 skipped under Wine Python 3.12. The skipped test asks real PowerShell and runs on the Windows CI runner.
- Full Python suite under Wine: the only failures are 5 that fail the same way on clean `develop` (4 in
  `test_launcher_close`, timing under Wine; `git check-attr` with no git under Wine).
- Linux suite on an Instabox box: 286 passed; the `git check-attr` test needs a git checkout and passes locally.
- Screenshot of the Log page under Wine: the button sits left of Clear, the saved line names the zip.

## Not checked

- PowerShell output on a real Windows PC other than the CI runner.
- RELEASING steps 3 and 4 (validation layer, 5-minute play run): no game on this machine.
