#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Installs HelixSR (experimental upscaler on Windows) into the port's helixsr folder in one step.

    fetch_helixsr.py [--dir DIR] [--yes]

HelixSR (https://github.com/lonewolf0622/HelixSR, a closed, free program that runs NVIDIA's DLSS network as
Direct3D 12 compute shaders) is not part of the port. This script does what a player did by hand: fetches the
pinned release zip, checks its SHA-256, extracts it (the zip holds a HelixSR-2.0.0 folder) and runs HelixSR's own
`helixsr-setup.ps1 -NoFg -Dlss FILE` in it. The tool downloads NVIDIA's DLSS 310.7.0 DLL itself (under NVIDIA's
license; pinned size and SHA-256, the values HelixSR's own setup checks), hands it over as `-Dlss` and deletes it
after the build, a failed one too. The setup's stdin is closed. The setup's own `-Yes` is never passed: 2.0.0's
setup hands it on to the per-game installer, which would then install HelixSR into every game it finds, and
without it that installer reads the closed stdin, gets the end of the input and changes no game. `-NoFg` skips
HelixSR's frame generation (the port uses HelixSR as an upscaler only), whose build downloads about 400 MB more of
NVIDIA's files. What the setup builds stays on this PC. Because of that, the command line refuses to build without
--yes, and the launcher asks first. Downloads are resumable
(HTTP Range into a .part file) and reuse fetch_fsr4vk's FetchError and hashing. Which pinned zip the extracted
files came from is recorded beside the folder (PACKAGE; `package_current`), the setup's whole output is kept as
SETUP_LOG, and `install` writes both so a build from another zip and a failed build stay visible after the run.

DIR defaults to the helixsr folder of the port, where the launcher looks (helixsr_folder()).
Exit status 0: the network is built (helixsr_setup.json sits in the release folder, one level above the
HelixSR-fsrfg folder that holds amd_fidelityfx_dx12.dll; 1.9.x kept both in the release folder). 1: failed (the
reason is the last line on stderr).
"""
import argparse
import http.client
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_fsr4vk import CHUNK, FetchError, sha256_of  # noqa: E402

VERSION = '2.0.0'
ZIP_NAME = 'HelixSR-2.0.0.zip'
ZIP_URL = f'https://github.com/lonewolf0622/HelixSR/releases/download/v{VERSION}/{ZIP_NAME}'
# The values GitHub publishes for the v2.0.0 asset (published 2026-10-19; it carries HelixSR Scaler's files too,
# hence the about 131 MB). A later re-upload makes the check fail with "does not match the pinned SHA-256" until
# the pins update.
ZIP_SIZE = 136997025
ZIP_SHA256 = '0d60686eddbb0abd680b27d12a088344b212464ebf0a2dc18d10387e1c817f52'
TOP = f'HelixSR-{VERSION}'  # the folder the zip extracts to
FSRFG = 'HelixSR-fsrfg'  # 2.0.0 keeps the DLL in this folder of the release; 1.9.x kept it in the release folder
DLL = 'amd_fidelityfx_dx12.dll'
BUILT = 'helixsr_setup.json'  # written in the release folder when the setup built the network
# NVIDIA's DLSS 310.7.0 DLL, which the setup builds HelixSR's network from: the same URL, size and SHA-256 as the
# setup's own helixsr_setup.py. This tool fetches it after the player's consent and passes it as -Dlss.
DLSS_NAME = 'nvngx_dlss.dll'
DLSS_URL = 'https://raw.githubusercontent.com/NVIDIA/DLSS/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll'
DLSS_SIZE = 58977904
DLSS_SHA256 = 'be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e'
NVIDIA_LICENSE_URL = 'https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt'
PACKAGE = 'helixsr-package.json'  # beside the extracted folder: which pinned zip its files came from
SETUP_LOG = 'helixsr-setup.log'  # the setup's output, kept beside the folder so a failed build stays readable
SETUP = 'helixsr-setup.ps1'
BUILD_TIMEOUT = 1800  # seconds; the build takes about a minute plus the first-time downloads
PORT = Path(__file__).resolve().parent.parent


def default_dir(port: Path = PORT) -> Path:
    return port / 'helixsr'


def install_folder(target: Path) -> Path:
    """The folder that holds the DLL once `fetch` and `build` have run."""
    return Path(target) / TOP


def dll_folder(folder: Path):
    """The folder that holds the DLL: HelixSR-fsrfg inside a 2.0.0 release folder, else the folder itself (1.9.x, or
    HelixSR-fsrfg given directly); None when neither has it."""
    return next((p for p in (Path(folder) / FSRFG, Path(folder)) if (p / DLL).is_file()), None)


def extracted(target: Path) -> bool:
    return (install_folder(target) / SETUP).is_file() and dll_folder(install_folder(target)) is not None


def built(folder: Path) -> bool:
    """True when the setup's record sits beside the DLL (1.9.x) or one folder above it (2.0.0)."""
    dll = dll_folder(folder)
    return dll is not None and any((p / BUILT).is_file() for p in (dll, dll.parent))


def _download(part: Path, progress) -> None:
    """Fills `part` with the zip (see download)."""
    download(part, progress, ZIP_URL, ZIP_SIZE)


def download(part: Path, progress, url: str, size: int) -> None:
    """Fills `part` with the file at `url` (exactly `size` bytes), resuming what an earlier run left; the caller
    checks the hash."""
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        if have == size:
            return
        request = urllib.request.Request(url, headers={'User-Agent': 'bbport-fetch-helixsr'})
        if have:
            request.add_header('Range', f'bytes={have}-')
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if have and response.status != 206:
                    have = 0  # the server ignored the range: start again
                with open(part, 'ab' if have else 'wb') as out:
                    for block in iter(lambda: response.read(CHUNK), b''):
                        out.write(block)
                        have += len(block)
                        if progress:
                            progress(have, size)
        except urllib.error.HTTPError as error:
            if error.code == 416:  # the part is longer than the file: drop it
                part.unlink(missing_ok=True)
            elif attempt == 3:
                raise FetchError(f'download failed: HTTP {error.code} from {url}')
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as error:
            if attempt == 3:
                raise FetchError(f'download failed: {error}')
        if part.exists() and part.stat().st_size == size:
            return
    raise FetchError('download failed: the file stayed incomplete')


def safe_members(archive: zipfile.ZipFile, target: Path):
    """[(member, destination)] for the zip; FetchError when one would land outside `target`."""
    root = Path(target).resolve()
    members = []
    for member in archive.infolist():
        destination = (root / member.filename).resolve()
        # A drive or root (C:/x, /x) is refused on every host: on Linux it would land inside `target`.
        if PureWindowsPath(member.filename).anchor or (destination != root and root not in destination.parents):
            raise FetchError(f'the zip holds a path outside the folder ({member.filename}); nothing was extracted')
        members.append((member, destination))
    return members


def extract(part: Path, target: Path) -> None:
    """Unpacks the zip into `target`, every entry checked first so a bad one leaves nothing behind."""
    try:
        with zipfile.ZipFile(part) as archive:
            for member, destination in safe_members(archive, target):
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + '.tmp')
                try:
                    with archive.open(member) as source, open(temporary, 'wb') as out:
                        for block in iter(lambda: source.read(CHUNK), b''):
                            out.write(block)
                    os.replace(temporary, destination)
                except OSError:  # e.g. the game has the DLL loaded: no half-written copy stays
                    temporary.unlink(missing_ok=True)
                    raise
    except zipfile.BadZipFile as error:
        raise FetchError(f'the zip is damaged: {error}')
    if not extracted(target):
        raise FetchError(f'the zip does not hold {TOP}\\{SETUP} and {TOP}\\{FSRFG}\\{DLL}')


def write_package_record(target: Path, folder: Path = None) -> None:
    """Remembers which pinned zip was extracted into `target`: `package_current` trusts a folder only with this
    record, so a copy built from another zip never passes for the pinned one. Written the moment the verified zip
    is on disk."""
    folder = Path(folder) if folder else install_folder(target)
    (Path(target) / PACKAGE).write_text(json.dumps(
        {'version': VERSION, 'zip_name': ZIP_NAME, 'zip_sha256': ZIP_SHA256, 'folder': folder.name},
        indent=1) + '\n', encoding='utf-8')


def package_current(target: Path) -> bool:
    """True when the pinned zip's files were extracted into `target` (see write_package_record) and they went to
    the pinned folder. A folder without that record, or one from another zip, counts as outdated."""
    try:
        record = json.loads((Path(target) / PACKAGE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (isinstance(record, dict) and record.get('zip_sha256') == ZIP_SHA256
            and record.get('folder') == TOP)


def forget_build(folder: Path) -> None:
    """Drops the build record of the files a fresh extraction just replaced, so a failure of the coming build
    cannot let the replaced build pass for the new files. build() writes it again; the zip carries none."""
    (Path(folder) / BUILT).unlink(missing_ok=True)


def fetch(target: Path, progress=None) -> Path:
    """Downloads, verifies and extracts the pinned release into `target`; returns the HelixSR folder.
    `progress(done, total)` is optional. Raises FetchError (OSError when a file cannot be written)."""
    target = Path(target)
    work = target / '.download'
    work.mkdir(parents=True, exist_ok=True)
    part = work / (ZIP_NAME + '.part')
    _download(part, progress)
    if part.stat().st_size != ZIP_SIZE or sha256_of(part) != ZIP_SHA256:
        part.unlink(missing_ok=True)
        raise FetchError('the downloaded zip does not match the pinned SHA-256; it was deleted')
    extract(part, target)
    folder = install_folder(target)
    forget_build(folder)             # the replaced folder's build record must not pass for these files
    write_package_record(target, folder)
    part.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    return folder


def fetch_dlss(target: Path, progress=None) -> Path:
    """Downloads NVIDIA's DLSS 310.7.0 DLL (pinned size and SHA-256) into `target`/.download and returns its path.
    The setup builds HelixSR's network from it; it asks for it itself only when its caller passes `-Yes`, which
    this tool cannot (see setup_command). The caller deletes it after the build (drop_dlss)."""
    work = Path(target) / '.download'
    work.mkdir(parents=True, exist_ok=True)
    part, path = work / (DLSS_NAME + '.part'), work / DLSS_NAME
    download(part, progress, DLSS_URL, DLSS_SIZE)
    if part.stat().st_size != DLSS_SIZE or sha256_of(part) != DLSS_SHA256:
        part.unlink(missing_ok=True)
        raise FetchError("the downloaded NVIDIA DLSS DLL does not match the pinned SHA-256; it was deleted")
    os.replace(part, path)
    return path


def drop_dlss(dlss: Path) -> None:
    """Deletes the DLSS DLL fetch_dlss made (NVIDIA's file: it is not kept) and its work folder when empty."""
    Path(dlss).unlink(missing_ok=True)
    try:
        Path(dlss).parent.rmdir()
    except OSError:
        pass


def setup_command(dlss=None) -> list:
    """HelixSR's own setup, run with the HelixSR folder as the working directory and stdin closed. `-NoFg` skips its
    frame generation: that build is the only thing that downloads NVIDIA's Streamline DLL and NGX core (about 400 MB
    from NVIDIA), and the port uses HelixSR as an upscaler only. The setup treats the frame generation as done
    then (its -NoFg switch maps to `--no-fg`, which makes helixsr_setup.py's build_fg() return at once). There is no
    `-Yes`: in 2.0.0 it also answers the per-game installer's questions with their defaults, which installs
    HelixSR into every game found. Without it that step reads the closed stdin, gets the end of the input and
    changes no game, so the player's consent is carried by the file this passes: the DLSS DLL (`-Dlss`). `dlss` is
    None only when the network is built already."""
    return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SETUP, '-NoFg',
            *(['-Dlss', str(dlss)] if dlss else [])]


class SetupLog:
    """Appends the setup's output to `path` and passes every line on to `sink`. The launcher's log box is gone with
    its window, so this file is what a failed build is diagnosed from afterwards (it rides in a bug report).
    Call it like an on_line function; a log that cannot be written never stops a build."""

    def __init__(self, path, sink=None):
        self.path, self.sink = (Path(path) if path else None), sink

    def write(self, text: str) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'a', encoding='utf-8') as stream:
                stream.write(text + '\n')
        except OSError:
            pass

    def start(self, heading: str) -> None:
        self.write(f'=== {time.strftime("%Y-%m-%d %H:%M:%S")} {heading} ===')

    def __call__(self, text: str) -> None:
        self.write(text)
        if self.sink:
            self.sink(text)


def build(folder: Path, on_line=None, encoding='utf-8', creationflags=0, log_path=None, dlss=None) -> None:
    """Runs the setup in `folder` (no console window) and passes each output line to `on_line`, keeping the whole
    output in `log_path` when one is given. Its stdin is closed, so no question (the per-game installer's) finds an
    answer. `dlss` is the DLSS DLL it builds from (fetch_dlss), needed unless `folder` is built already. Raises
    FetchError unless it ends with the network built (BUILT in the release folder). The setup builds only what the
    folder is missing (HelixSR's own scripts do): a folder whose network is built does not get a new BUILT record,
    so one that is already there is kept."""
    folder = Path(folder)
    log = SetupLog(log_path, on_line)
    if not built(folder):  # a file of an earlier build must not pass for this one; the build's own record is kept
        (folder / BUILT).unlink(missing_ok=True)
    command = setup_command(None if built(folder) else dlss)
    try:
        process = subprocess.Popen(command, cwd=str(folder), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=creationflags)
    except OSError as error:
        raise FetchError(f'could not start PowerShell: {error}')
    timer = threading.Timer(BUILD_TIMEOUT, process.kill)
    timer.start()
    log.start(f"HelixSR's setup: {' '.join(command)} (in {folder})")
    tail = []
    try:
        for raw in iter(process.stdout.readline, b''):
            line = raw.decode(encoding, errors='replace').rstrip()
            if line:
                tail = (tail + [line])[-5:]
                log(line)
        code = process.wait()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
    if code != 0:
        raise FetchError(f'HelixSR setup failed (exit {code}): {tail[-1] if tail else "no output"}')
    if not built(folder):
        raise FetchError(f'HelixSR setup finished but {BUILT} is missing in {folder}')


def install(target: Path, progress=None, on_line=None, encoding='utf-8', creationflags=0, force=False) -> Path:
    """Fetch (unless the folder is already extracted and `force` is off), then build. Returns the folder. The
    setup's output is kept in `target` as SETUP_LOG, and `fetch` records there which pinned zip the files came
    from (PACKAGE), so a bug report has both. NVIDIA's DLSS DLL is fetched for the build and deleted after it, a
    failed one too; a folder whose network is built needs none."""
    if force or not extracted(target):
        fetch(target, progress)
    folder = install_folder(target)
    dlss = None if built(folder) else fetch_dlss(target, progress)
    try:
        build(folder, on_line, encoding, creationflags, Path(target) / SETUP_LOG, dlss=dlss)
    finally:
        if dlss:
            drop_dlss(dlss)
    return folder


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dir', type=Path, default=None, help='target folder (default: the port\'s helixsr folder)')
    parser.add_argument('--yes', action='store_true',
                        help=f"agree that NVIDIA's DLSS 310.7.0 DLL is downloaded under NVIDIA's license "
                             f'({NVIDIA_LICENSE_URL}) and given to HelixSR\'s setup')
    args = parser.parse_args(argv)
    if not args.yes:
        print(f"fetch_helixsr: this downloads NVIDIA's DLSS DLL under NVIDIA's license ({NVIDIA_LICENSE_URL}), "
              'gives it to HelixSR\'s setup, which builds the network on this PC, and deletes the DLL. '
              'Run again with --yes to agree.', file=sys.stderr)
        return 1
    last = [-1]

    def progress(done, total):
        percent = done * 100 // total
        if percent != last[0] and percent % 5 == 0:
            last[0] = percent
            print(f'PROGRESS {done} {total}', flush=True)
    try:
        folder = install(args.dir or default_dir(), progress, lambda line: print(line, flush=True),
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (FetchError, OSError) as error:
        print(f'fetch_helixsr: {error}', file=sys.stderr)
        return 1
    print(f'HelixSR {VERSION}: network built in {folder}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
