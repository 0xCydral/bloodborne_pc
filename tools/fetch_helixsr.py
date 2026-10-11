#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Installs HelixSR (experimental upscaler on Windows) into the port's helixsr folder in one step.

    fetch_helixsr.py [--dir DIR] [--yes]

HelixSR (https://github.com/lonewolf0622/HelixSR, a closed, free program that runs NVIDIA's DLSS network as
Direct3D 12 compute shaders) is not part of the port. This script does what a player did by hand: fetches the
pinned release zip, checks its SHA-256, extracts it (the zip holds a HelixSR-1.8.1 folder) and runs HelixSR's own,
unmodified `helixsr-setup.ps1 -Yes` in it, with stdin closed so the per-game installer skips every game. `-Yes`
accepts the download of NVIDIA's DLSS DLL under NVIDIA's license, which the setup then deletes; the network it
builds stays on this PC. Because of that, the command line refuses to build without --yes, and the launcher asks
first. Download is resumable (HTTP Range into a .part file) and reuses fetch_fsr4vk's FetchError and hashing.

DIR defaults to the helixsr folder of the port, where the launcher looks (helixsr_folder()).
Exit status 0: the network is built (helixsr_setup.json sits beside amd_fidelityfx_dx12.dll). 1: failed (the
reason is the last line on stderr).
"""
import argparse
import http.client
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_fsr4vk import CHUNK, FetchError, sha256_of  # noqa: E402

VERSION = '1.8.1'
ZIP_NAME = f'HelixSR-{VERSION}.zip'
ZIP_URL = f'https://github.com/lonewolf0622/HelixSR/releases/download/v{VERSION}/{ZIP_NAME}'
# The v1.8.1 asset was uploaded again on 2026-10-11 (16:49 UTC: new dlss-loader\dxgi.dll and setup\helixsr_install.py,
# the same DLL and helixsr-setup.ps1); these are the values GitHub publishes for it now. A later re-upload makes the
# check fail with "does not match the pinned SHA-256" until the pins are updated.
ZIP_SIZE = 56650779
ZIP_SHA256 = '5b2d06db22d59aaa8c931fc06d3a8e354e65dde1bef9fce90086542f02d4a627'
TOP = f'HelixSR-{VERSION}'  # the folder the zip extracts to
DLL = 'amd_fidelityfx_dx12.dll'
BUILT = 'helixsr_setup.json'  # written beside the DLL when the setup built the network
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
        if destination != root and root not in destination.parents:
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
    part.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    return install_folder(target)


def setup_command() -> list:
    """HelixSR's own setup, unmodified; run with the HelixSR folder as the working directory."""
    return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SETUP, '-Yes']


def build(folder: Path, on_line=None, encoding='utf-8', creationflags=0) -> None:
    """Runs the setup in `folder` (stdin closed, no console window) and passes each output line to `on_line`.
    Raises FetchError unless it ends with the network built (BUILT beside the DLL)."""
    folder = Path(folder)
    (folder / BUILT).unlink(missing_ok=True)  # a file of an earlier build must not pass for this one
    try:
        process = subprocess.Popen(setup_command(), cwd=str(folder), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=creationflags)
    except OSError as error:
        raise FetchError(f'could not start PowerShell: {error}')
    timer = threading.Timer(BUILD_TIMEOUT, process.kill)
    timer.start()
    tail = []
    try:
        for raw in iter(process.stdout.readline, b''):
            line = raw.decode(encoding, errors='replace').rstrip()
            if line:
                tail = (tail + [line])[-5:]
                if on_line:
                    on_line(line)
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
    """Fetch (unless the folder is already extracted and `force` is off), then build. Returns the folder."""
    if force or not extracted(target):
        fetch(target, progress)
    folder = install_folder(target)
    build(folder, on_line, encoding, creationflags)
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
