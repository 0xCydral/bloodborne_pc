#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Installs HelixSR (experimental upscaler on Windows) into the port's helixsr folder in one step.

    fetch_helixsr.py [--dir DIR] [--yes]

HelixSR (https://github.com/lonewolf0622/HelixSR, a closed, free program that runs NVIDIA's DLSS network as
Direct3D 12 compute shaders) is not part of the port. This script does what a player did by hand: fetches the
pinned release zip, checks its SHA-256, extracts it (the zip holds a HelixSR-1.9.0 folder) and runs HelixSR's own
`helixsr-setup.ps1 -Yes -NoFg` in it, with stdin closed so the per-game installer skips every game. `-Yes`
accepts the download of NVIDIA's DLSS DLL under NVIDIA's license, which the setup then deletes; `-NoFg` skips
HelixSR's frame generation (the port uses HelixSR as an upscaler only), whose build downloads about 400 MB more
of NVIDIA's files. What the setup builds stays on this PC. Because of that, the command line refuses to build
without --yes, and the launcher asks first. Download is resumable (HTTP Range into a .part file) and reuses
fetch_fsr4vk's FetchError and hashing. Which pinned zip the extracted files came from is recorded beside the
folder (PACKAGE; `package_current`), the setup's whole output is kept as SETUP_LOG, and `install` writes both so
a build from another zip and a failed build stay visible after the run.

DIR defaults to the helixsr folder of the port, where the launcher looks (helixsr_folder()).
Exit status 0: the network is built (helixsr_setup.json sits beside amd_fidelityfx_dx12.dll). 1: failed (the
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

VERSION = '1.9.0'
ZIP_NAME = 'HelixSR-1.9.0.zip'
ZIP_URL = f'https://github.com/lonewolf0622/HelixSR/releases/download/v{VERSION}/{ZIP_NAME}'
# The values GitHub publishes for the v1.9.0 asset (re-uploaded 2026-10-15; the earlier HelixSR-1.9.0.2.zip was
# removed). A later re-upload makes the check fail with "does not match the pinned SHA-256" until the pins update.
ZIP_SIZE = 3803249
ZIP_SHA256 = '795268f60a1262cd7c8cb01a38f553751cc380fa0cbec63c8f466b6807468be4'
TOP = f'HelixSR-{VERSION}'  # the folder the zip extracts to
DLL = 'amd_fidelityfx_dx12.dll'
BUILT = 'helixsr_setup.json'  # written beside the DLL when the setup built the network
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


def extracted(target: Path) -> bool:
    return (install_folder(target) / SETUP).is_file() and (install_folder(target) / DLL).is_file()


def built(folder: Path) -> bool:
    return (Path(folder) / BUILT).is_file() and (Path(folder) / DLL).is_file()


def _download(part: Path, progress) -> None:
    """Fills `part` with the zip, resuming what an earlier run left; the caller checks the hash."""
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        if have > ZIP_SIZE:
            part.unlink()
            have = 0
        if have == ZIP_SIZE:
            return
        request = urllib.request.Request(ZIP_URL, headers={'User-Agent': 'bbport-fetch-helixsr'})
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
                            progress(have, ZIP_SIZE)
        except urllib.error.HTTPError as error:
            if error.code == 416:  # the part is longer than the file: drop it
                part.unlink(missing_ok=True)
            elif attempt == 3:
                raise FetchError(f'download failed: HTTP {error.code} from {ZIP_URL}')
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as error:
            if attempt == 3:
                raise FetchError(f'download failed: {error}')
        if part.exists() and part.stat().st_size == ZIP_SIZE:
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
        raise FetchError(f'the zip does not hold {TOP}\\{SETUP} and {TOP}\\{DLL}')


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


def setup_command() -> list:
    """HelixSR's own setup, run with the HelixSR folder as the working directory. `-NoFg` skips its frame
    generation: that build is the only thing that downloads NVIDIA's Streamline DLL and NGX core (about 400 MB
    from NVIDIA), and the port uses HelixSR as an upscaler only. The setup treats the frame generation as done
    then (its -NoFg switch maps to `--no-fg`, which makes helixsr_setup.py's build_fg() return at once)."""
    return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SETUP, '-Yes', '-NoFg']


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


def build(folder: Path, on_line=None, encoding='utf-8', creationflags=0, log_path=None) -> None:
    """Runs the setup in `folder` (stdin closed, no console window) and passes each output line to `on_line`,
    keeping the whole output in `log_path` when one is given. Raises FetchError unless it ends with the network
    built (BUILT beside the DLL). The setup builds only what the folder is missing (HelixSR's own scripts do): a
    folder whose network is built does not get a new BUILT record, so one that is already there is kept."""
    folder = Path(folder)
    log = SetupLog(log_path, on_line)
    if not built(folder):  # a file of an earlier build must not pass for this one; the build's own record is kept
        (folder / BUILT).unlink(missing_ok=True)
    try:
        process = subprocess.Popen(setup_command(), cwd=str(folder), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=creationflags)
    except OSError as error:
        raise FetchError(f'could not start PowerShell: {error}')
    timer = threading.Timer(BUILD_TIMEOUT, process.kill)
    timer.start()
    log.start(f"HelixSR's setup: {' '.join(setup_command())} (in {folder})")
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
    from (PACKAGE), so a bug report has both."""
    if force or not extracted(target):
        fetch(target, progress)
    folder = install_folder(target)
    build(folder, on_line, encoding, creationflags, Path(target) / SETUP_LOG)
    return folder


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dir', type=Path, default=None, help='target folder (default: the port\'s helixsr folder)')
    parser.add_argument('--yes', action='store_true',
                        help="agree that the setup downloads NVIDIA's DLSS 310.7.0 DLL under NVIDIA's license "
                             '(https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt)')
    args = parser.parse_args(argv)
    if not args.yes:
        print("fetch_helixsr: HelixSR's setup downloads NVIDIA's DLSS DLL under NVIDIA's license "
              '(https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt), builds the network on this PC and '
              'deletes the DLL. Run again with --yes to agree.', file=sys.stderr)
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
