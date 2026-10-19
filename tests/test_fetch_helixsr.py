"""tools/fetch_helixsr.py: pinned download and hash, safe extraction, the setup command line and when a build counts
as done (a local server, a synthetic zip, a fake PowerShell; nothing touches the network)."""
from paths import ROOT
import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from test_fetch_fsr4vk import Server

spec = importlib.util.spec_from_file_location("fetch_helixsr", ROOT / "tools/fetch_helixsr.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)

TOP = fetch.TOP
MEMBERS = {
    f"{TOP}/": b"",
    f"{TOP}/HelixSR-fsrfg/amd_fidelityfx_dx12.dll": b"MZ" + bytes(range(256)) * 8,
    f"{TOP}/helixsr-setup.ps1": b"# setup",
    f"{TOP}/setup/helixsr_setup.py": b"print('x')",
}


def make_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeProcess:
    """What subprocess.Popen returns: some output and an exit code."""

    def __init__(self, output=b"", code=0):
        self.stdout, self.code = io.BytesIO(output), code

    def wait(self):
        return self.code

    def poll(self):
        return self.code

    def kill(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "helixsr"

    def pin(self, body, **overrides):
        values = dict(ZIP_SIZE=len(body), ZIP_SHA256=hashlib.sha256(body).hexdigest(), **overrides)
        for name, value in values.items():
            patcher = mock.patch.object(fetch, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def serve(self, body, **kwargs):
        server = Server(body, **kwargs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.pin(body, ZIP_URL=f"http://127.0.0.1:{server.server_address[1]}/helixsr.zip")
        return server


class FetchTests(Base):
    def test_the_pins_are_the_verified_release(self):
        self.assertEqual(fetch.ZIP_URL, "https://github.com/lonewolf0622/HelixSR/releases/download/v2.0.0/HelixSR-2.0.0.zip")
        self.assertEqual(fetch.ZIP_SIZE, 136997025)
        self.assertEqual(fetch.ZIP_SHA256, "0d60686eddbb0abd680b27d12a088344b212464ebf0a2dc18d10387e1c817f52")

    def test_extraction_layout(self):
        body = make_zip(MEMBERS)
        self.serve(body)
        seen = []
        folder = fetch.fetch(self.target, lambda done, total: seen.append((done, total)))
        self.assertEqual(folder, self.target / TOP)
        self.assertTrue((folder / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll").is_file())
        self.assertEqual(fetch.dll_folder(folder), folder / "HelixSR-fsrfg")
        self.assertEqual((folder / "setup" / "helixsr_setup.py").read_bytes(), b"print('x')")
        self.assertTrue(fetch.extracted(self.target))
        self.assertFalse((self.target / ".download").exists())  # the zip is not kept
        self.assertEqual(seen[-1], (len(body), len(body)))

    def test_hash_mismatch_is_rejected_and_nothing_is_extracted(self):
        body = make_zip(MEMBERS)
        self.serve(body)
        fetch.ZIP_SHA256 = "0" * 64
        with self.assertRaisesRegex(fetch.FetchError, "SHA-256"):
            fetch.fetch(self.target)
        self.assertFalse((self.target / TOP).exists())
        self.assertFalse(list((self.target / ".download").glob("*")))  # the bad download was deleted

    def test_zip_slip_is_rejected_before_anything_is_written(self):
        for evil in ("../evil.txt", f"{TOP}/../../evil.txt", "C:/Windows/evil.txt", "/evil.txt"):
            with self.subTest(evil=evil):
                body = make_zip({**MEMBERS, evil: b"x"})
                self.serve(body)
                self.target = self.root / f"helixsr{abs(hash(evil))}"  # no .part of an earlier case to resume
                with self.assertRaisesRegex(fetch.FetchError, "outside the folder"):
                    fetch.fetch(self.target)
                self.assertFalse((self.target / TOP / "helixsr-setup.ps1").exists())  # the good entries were not unpacked
                self.assertFalse((self.root / "evil.txt").exists())
                self.assertFalse((self.target.parent / "evil.txt").exists())

    def test_a_zip_without_the_setup_is_refused(self):
        body = make_zip({f"{TOP}/HelixSR-fsrfg/amd_fidelityfx_dx12.dll": b"MZ"})
        self.serve(body)
        with self.assertRaisesRegex(fetch.FetchError, "does not hold"):
            fetch.fetch(self.target)

    def test_a_cut_download_resumes_with_a_range(self):
        body = make_zip(MEMBERS)
        server = self.serve(body, cut_after=len(body) // 2)
        fetch.fetch(self.target)
        self.assertTrue(fetch.extracted(self.target))
        self.assertIsNone(server.ranges[0])
        self.assertEqual(server.ranges[1], f"bytes={len(body) // 2}-")

    def test_fetch_records_which_pinned_zip_the_files_came_from(self):
        self.serve(make_zip(MEMBERS))
        folder = fetch.fetch(self.target)
        self.assertTrue(fetch.package_current(self.target))
        record = json.loads((self.target / fetch.PACKAGE).read_text(encoding="utf-8"))
        self.assertEqual(record["zip_sha256"], fetch.ZIP_SHA256)
        self.assertEqual(record["zip_name"], fetch.ZIP_NAME)
        self.assertEqual(record["folder"], folder.name)

    def test_a_folder_without_the_record_or_from_another_zip_is_outdated(self):
        self.assertFalse(fetch.package_current(self.target))  # nothing extracted yet
        self.serve(make_zip(MEMBERS))
        fetch.fetch(self.target)
        for other in ("0" * 64, 1):  # another zip's hash; a record that is not even shaped right
            (self.target / fetch.PACKAGE).write_text(json.dumps({"zip_sha256": other, "folder": fetch.TOP}),
                                                     encoding="utf-8")
            self.assertFalse(fetch.package_current(self.target))
        (self.target / fetch.PACKAGE).write_text(json.dumps({"zip_sha256": fetch.ZIP_SHA256, "folder": "HelixSR-1.8.1"}),
                                                 encoding="utf-8")
        self.assertFalse(fetch.package_current(self.target))  # the files went to another release's folder
        (self.target / fetch.PACKAGE).write_text("not json", encoding="utf-8")
        self.assertFalse(fetch.package_current(self.target))

    def test_a_refetch_drops_the_replaced_build_record_and_records_the_zip(self):
        self.serve(make_zip(MEMBERS))
        folder = fetch.fetch(self.target)
        (folder / fetch.BUILT).write_text("{}", encoding="utf-8")  # a build of the replaced files
        (self.target / fetch.PACKAGE).write_text("{}", encoding="utf-8")
        fetch.fetch(self.target)
        self.assertFalse((folder / fetch.BUILT).exists())
        self.assertTrue(fetch.package_current(self.target))


class LayoutTests(Base):
    """Where the DLL and the setup's record are: 2.0.0 has the DLL in HelixSR-fsrfg and the record one folder up;
    a 1.9.x folder (the Update case) has both in the release folder."""

    def release(self, name, nested):
        folder = self.root / name
        dll = folder / "HelixSR-fsrfg" if nested else folder
        dll.mkdir(parents=True)
        (dll / fetch.DLL).write_bytes(b"MZ")
        return folder, dll

    def test_2_0_layout_is_built_with_the_record_in_the_release_folder(self):
        folder, dll = self.release("HelixSR-2.0.0", nested=True)
        self.assertEqual(fetch.dll_folder(folder), dll)
        self.assertFalse(fetch.built(folder))
        (folder / fetch.BUILT).write_text("{}", encoding="utf-8")
        self.assertTrue(fetch.built(folder))
        self.assertTrue(fetch.built(dll))  # asked about the DLL folder itself, the same answer

    def test_a_record_inside_the_fsrfg_folder_counts_too(self):
        folder, dll = self.release("HelixSR-2.0.0", nested=True)
        (dll / fetch.BUILT).write_text("{}", encoding="utf-8")
        self.assertTrue(fetch.built(folder))

    def test_1_9_layout_still_counts_as_built(self):
        folder, dll = self.release("HelixSR-1.9.1", nested=False)
        self.assertEqual(fetch.dll_folder(folder), folder)
        self.assertFalse(fetch.built(folder))
        (folder / fetch.BUILT).write_text("{}", encoding="utf-8")
        self.assertTrue(fetch.built(folder))

    def test_no_dll_is_never_built(self):
        folder = self.root / "HelixSR-2.0.0"
        (folder / "HelixSR-fsrfg").mkdir(parents=True)
        (folder / fetch.BUILT).write_text("{}", encoding="utf-8")
        self.assertIsNone(fetch.dll_folder(folder))
        self.assertFalse(fetch.built(folder))

    def test_a_1_9_folder_is_extracted_when_it_is_the_pinned_name(self):
        # the extracted() gate follows the DLL, not the version: a flat folder under the pinned name still passes it
        folder = self.target / TOP
        folder.mkdir(parents=True)
        (folder / fetch.DLL).write_bytes(b"MZ")
        (folder / fetch.SETUP).write_text("#", encoding="utf-8")
        self.assertTrue(fetch.extracted(self.target))


class BuildTests(Base):
    def setUp(self):
        super().setUp()
        self.folder = self.target / TOP
        (self.folder / "HelixSR-fsrfg").mkdir(parents=True)
        (self.folder / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll").write_bytes(b"MZ")
        (self.folder / "helixsr-setup.ps1").write_text("#", encoding="utf-8")

    def writes_marker(self):
        (self.folder / fetch.BUILT).write_text("{}", encoding="utf-8")

    def runs(self, output=b"", code=0, marker=False):
        """A Popen stand-in: when the setup 'starts' it may write the marker, like the real one when it succeeds."""
        def start(*_args, **_kwargs):
            if marker:
                self.writes_marker()
            return FakeProcess(output, code)
        return start

    def test_the_command_line_is_the_setup_with_the_dll_and_no_frame_generation(self):
        # -NoFg is what keeps the about 400 MB of NVIDIA downloads for HelixSR's frame generation away. -Dlss hands
        # the setup NVIDIA's DLL, so it asks nothing. No -Yes: in 2.0.0 it is also handed to the per-game installer,
        # which then installs HelixSR into every game it finds.
        base = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "helixsr-setup.ps1", "-NoFg"]
        self.assertEqual(fetch.setup_command(), base)  # a built network needs no DLL
        self.assertEqual(fetch.setup_command("n.dll"), base + ["-Dlss", "n.dll"])

    def test_no_command_that_reaches_the_setup_carries_yes(self):
        commands = [fetch.setup_command(), fetch.setup_command("n.dll")]
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs(marker=True)) as popen:
            fetch.build(self.folder, dlss=self.root / "nvngx_dlss.dll")
        commands.append(popen.call_args[0][0])
        for command in commands:
            self.assertFalse({"-yes", "--yes", "-y"} & {part.lower() for part in command}, command)
            self.assertIn("-NoFg", command)
        self.assertEqual(commands[-1][-2:], ["-Dlss", str(self.root / "nvngx_dlss.dll")])  # NVIDIA's DLL is the consent

    def test_it_runs_in_the_folder_with_stdin_closed_and_no_window(self):
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs(b"building\r\n", marker=True)) as popen:
            fetch.build(self.folder, creationflags=0x08000000, dlss=Path("n.dll"))
        args, kwargs = popen.call_args
        self.assertEqual(args[0], fetch.setup_command("n.dll"))
        self.assertEqual(kwargs["cwd"], str(self.folder))
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)  # no question finds an answer, the per-game installer's included
        self.assertIs(kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["creationflags"], 0x08000000)

    def test_a_built_folder_gets_no_dll_argument(self):
        self.writes_marker()
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs()) as popen:
            fetch.build(self.folder, dlss=Path("n.dll"))
        self.assertEqual(popen.call_args[0][0], fetch.setup_command())

    def test_output_lines_are_streamed_in_order(self):
        lines = []
        with mock.patch.object(fetch.subprocess, "Popen",
                               side_effect=self.runs("[helixsr-setup] one\r\n\r\ntwo é\r\n".encode("cp850"), marker=True)):
            fetch.build(self.folder, lines.append, encoding="cp850")
        self.assertEqual(lines, ["[helixsr-setup] one", "two é"])

    def test_success_is_the_marker_beside_the_dll(self):
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs(marker=True)):
            fetch.build(self.folder)
        self.assertTrue(fetch.built(self.folder))

    def test_exit_zero_without_the_marker_is_a_failure(self):
        with mock.patch.object(fetch.subprocess, "Popen", return_value=FakeProcess(b"all fine\n", 0)):
            with self.assertRaisesRegex(fetch.FetchError, "helixsr_setup.json is missing"):
                fetch.build(self.folder)

    def test_a_record_the_setup_did_not_rewrite_is_kept(self):
        # with the network built, the setup does not write helixsr_setup.json again; keeping the record is
        # what lets a re-run (and the command line) pass instead of failing on a missing file
        self.writes_marker()
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs()):
            fetch.build(self.folder)
        self.assertTrue(fetch.built(self.folder))

    def test_the_setup_output_is_kept_in_the_log(self):
        log = self.target / "helixsr-setup.log"
        lines = []
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs(b"[helixsr-setup] one\nbuilding\r\n",
                                                                               marker=True)):
            fetch.build(self.folder, lines.append, log_path=log)
        text = log.read_text(encoding="utf-8")
        self.assertIn(f"HelixSR's setup: {' '.join(fetch.setup_command())}", text)
        self.assertIn("[helixsr-setup] one", text)
        self.assertIn("building", text)
        self.assertEqual(lines, ["[helixsr-setup] one", "building"])  # the sink still gets every line

    def test_a_failed_setup_keeps_its_output_in_the_log(self):
        log = self.target / "helixsr-setup.log"
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs(b"error: checksum mismatch\n", 1)):
            with self.assertRaises(fetch.FetchError):
                fetch.build(self.folder, log_path=log)
        self.assertIn("checksum mismatch", log.read_text(encoding="utf-8"))

    def test_a_log_that_cannot_be_written_never_stops_the_build(self):
        log = self.target / "a-folder"  # opening a folder as a file fails; the build must run anyway
        log.mkdir()
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs(marker=True)):
            fetch.build(self.folder, log_path=log)
        self.assertTrue(fetch.built(self.folder))

    def test_a_failing_setup_names_its_last_line(self):
        output = b"[helixsr-setup] downloading python.zip\n[helixsr-setup] error: checksum mismatch\n"
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=self.runs(output, 1)):
            with self.assertRaisesRegex(fetch.FetchError, r"exit 1\): \[helixsr-setup\] error: checksum mismatch"):
                fetch.build(self.folder)

    def test_powershell_that_cannot_start_is_reported(self):
        with mock.patch.object(fetch.subprocess, "Popen", side_effect=FileNotFoundError("powershell")):
            with self.assertRaisesRegex(fetch.FetchError, "could not start PowerShell"):
                fetch.build(self.folder)


class DlssTests(Base):
    """NVIDIA's DLSS DLL: pinned like the setup's own, SHA-checked, and never kept."""

    def serve_dlss(self, blob):
        for name, value in (("DLSS_SIZE", len(blob)), ("DLSS_SHA256", hashlib.sha256(blob).hexdigest())):
            patcher = mock.patch.object(fetch, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(fetch, "download", lambda part, progress, url, size: part.write_bytes(blob))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_pins_are_the_ones_helixsrs_own_setup_checks(self):
        self.assertEqual(fetch.DLSS_URL, "https://raw.githubusercontent.com/NVIDIA/DLSS/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll")
        self.assertEqual(fetch.DLSS_SIZE, 58977904)
        self.assertEqual(fetch.DLSS_SHA256, "be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e")
        self.assertEqual(fetch.NVIDIA_LICENSE_URL, "https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt")

    def test_the_dll_is_checked_and_then_deleted(self):
        self.serve_dlss(b"abc")
        path = fetch.fetch_dlss(self.target)
        self.assertEqual(path, self.target / ".download" / fetch.DLSS_NAME)
        self.assertEqual(path.read_bytes(), b"abc")
        fetch.drop_dlss(path)
        self.assertFalse(path.exists())
        self.assertFalse((self.target / ".download").exists())

    def test_a_dll_with_the_wrong_hash_is_rejected_and_deleted(self):
        self.serve_dlss(b"abc")
        with mock.patch.object(fetch, "download", lambda part, progress, url, size: part.write_bytes(b"abd")):
            with self.assertRaisesRegex(fetch.FetchError, "SHA-256"):
                fetch.fetch_dlss(self.target)
        self.assertEqual(list((self.target / ".download").iterdir()), [])

    def test_the_download_goes_to_the_dll_url(self):
        calls = []
        def record(part, progress, url, size):
            calls.append((url, size))
            part.write_bytes(b"")
        with mock.patch.object(fetch, "download", record):
            with self.assertRaises(fetch.FetchError):  # an empty file is not the pinned DLL
                fetch.fetch_dlss(self.target)
        self.assertEqual(calls, [(fetch.DLSS_URL, fetch.DLSS_SIZE)])


class InstallTests(Base):
    def extracted_folder(self):
        folder = self.target / TOP
        folder.mkdir(parents=True)
        (folder / "HelixSR-fsrfg").mkdir()
        (folder / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll").write_bytes(b"MZ")
        (folder / "helixsr-setup.ps1").write_text("#", encoding="utf-8")
        return folder

    def fake_dlss(self, target, progress=None):
        work = Path(target) / ".download"
        work.mkdir(parents=True, exist_ok=True)
        (work / fetch.DLSS_NAME).write_bytes(b"NVIDIA DLSS")
        return work / fetch.DLSS_NAME

    def test_an_extracted_folder_is_not_downloaded_again_unless_forced(self):
        folder = self.extracted_folder()
        with mock.patch.object(fetch, "fetch") as download, mock.patch.object(fetch, "build") as run, \
                mock.patch.object(fetch, "fetch_dlss", side_effect=self.fake_dlss):
            self.assertEqual(fetch.install(self.target), folder)
            download.assert_not_called()
            run.assert_called_once()
            fetch.install(self.target, force=True)
            download.assert_called_once()

    def test_a_fresh_target_downloads_then_builds_in_that_order(self):
        order = []
        with mock.patch.object(fetch, "fetch", side_effect=lambda *a: order.append("fetch")), \
                mock.patch.object(fetch, "fetch_dlss", side_effect=lambda *a: order.append("dlss") or Path("n.dll")), \
                mock.patch.object(fetch, "build", side_effect=lambda *a, **k: order.append("build")):
            fetch.install(self.target)
        self.assertEqual(order, ["fetch", "dlss", "build"])

    def test_the_setup_log_lands_in_the_target(self):
        self.extracted_folder()
        with mock.patch.object(fetch, "fetch"), mock.patch.object(fetch, "fetch_dlss", side_effect=self.fake_dlss), \
                mock.patch.object(fetch, "build") as run:
            fetch.install(self.target)
        self.assertEqual(run.call_args[0][4], self.target / "helixsr-setup.log")

    def test_the_build_gets_the_dll_and_it_is_deleted_after_it(self):
        folder = self.extracted_folder()
        seen = []

        def start(command, **_kwargs):
            seen.append((command, Path(command[-1]).read_bytes()))  # the DLL is there while the setup runs
            (folder / fetch.BUILT).write_text("{}", encoding="utf-8")
            return FakeProcess()
        with mock.patch.object(fetch, "fetch_dlss", side_effect=self.fake_dlss), \
                mock.patch.object(fetch.subprocess, "Popen", side_effect=start):
            fetch.install(self.target)
        command, blob = seen[0]
        self.assertEqual(command[-2], "-Dlss")
        self.assertEqual(blob, b"NVIDIA DLSS")
        self.assertFalse((self.target / ".download").exists())

    def test_the_dll_is_deleted_after_a_failed_build_too(self):
        self.extracted_folder()
        with mock.patch.object(fetch, "fetch_dlss", side_effect=self.fake_dlss), \
                mock.patch.object(fetch.subprocess, "Popen", return_value=FakeProcess(b"boom\n", 1)):
            with self.assertRaisesRegex(fetch.FetchError, "exit 1"):
                fetch.install(self.target)
        self.assertFalse((self.target / ".download" / fetch.DLSS_NAME).exists())

    def test_a_built_folder_downloads_no_dll(self):
        folder = self.extracted_folder()
        (folder / fetch.BUILT).write_text("{}", encoding="utf-8")
        with mock.patch.object(fetch, "fetch_dlss", side_effect=AssertionError("a built folder needs no DLL")), \
                mock.patch.object(fetch.subprocess, "Popen", return_value=FakeProcess()) as popen:
            self.assertEqual(fetch.install(self.target), folder)
        self.assertNotIn("-Dlss", popen.call_args[0][0])

    def test_the_command_line_runs_nothing_without_yes(self):
        err = io.StringIO()
        with mock.patch.object(fetch, "install") as install, contextlib.redirect_stderr(err):
            self.assertEqual(fetch.main(["--dir", str(self.target)]), 1)
        install.assert_not_called()
        self.assertIn("NVIDIA", err.getvalue())

    def test_the_command_line_installs_with_yes(self):
        with mock.patch.object(fetch, "install", return_value=self.target / TOP) as install, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fetch.main(["--yes", "--dir", str(self.target)]), 0)
        self.assertEqual(install.call_args[0][0], self.target)


if __name__ == "__main__":
    unittest.main()
