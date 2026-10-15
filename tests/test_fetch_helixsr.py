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
    f"{TOP}/amd_fidelityfx_dx12.dll": b"MZ" + bytes(range(256)) * 8,
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
    """What subprocess.Popen returns: some output, an exit code, and an optional side effect when it 'runs'."""

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
        self.assertEqual(fetch.ZIP_URL, "https://github.com/lonewolf0622/HelixSR/releases/download/v1.9.0/HelixSR-1.9.0.zip")
        self.assertEqual(fetch.ZIP_SIZE, 3803249)
        self.assertEqual(fetch.ZIP_SHA256, "795268f60a1262cd7c8cb01a38f553751cc380fa0cbec63c8f466b6807468be4")

    def test_extraction_layout(self):
        body = make_zip(MEMBERS)
        self.serve(body)
        seen = []
        folder = fetch.fetch(self.target, lambda done, total: seen.append((done, total)))
        self.assertEqual(folder, self.target / TOP)
        self.assertTrue((folder / "amd_fidelityfx_dx12.dll").is_file())
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
        body = make_zip({f"{TOP}/amd_fidelityfx_dx12.dll": b"MZ"})
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


class BuildTests(Base):
    def setUp(self):
        super().setUp()
        self.folder = self.target / TOP
        self.folder.mkdir(parents=True)
        (self.folder / "amd_fidelityfx_dx12.dll").write_bytes(b"MZ")
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

    def test_the_command_line_is_the_unmodified_setup_with_yes(self):
        self.assertEqual(fetch.setup_command(), ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                                 "helixsr-setup.ps1", "-Yes"])

    def test_it_runs_in_the_folder_with_stdin_closed_and_no_window(self):
        with mock.patch.object(fetch.subprocess, "Popen",
                               side_effect=self.runs(b"building\r\n", marker=True)) as popen:
            fetch.build(self.folder, creationflags=0x08000000)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], fetch.setup_command())
        self.assertEqual(kwargs["cwd"], str(self.folder))
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["creationflags"], 0x08000000)

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
        # with the network built, 1.9.0's setup does not write helixsr_setup.json again; keeping the record is
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


class InstallTests(Base):
    def test_an_extracted_folder_is_not_downloaded_again_unless_forced(self):
        folder = self.target / TOP
        folder.mkdir(parents=True)
        (folder / "amd_fidelityfx_dx12.dll").write_bytes(b"MZ")
        (folder / "helixsr-setup.ps1").write_text("#", encoding="utf-8")
        with mock.patch.object(fetch, "fetch") as download, mock.patch.object(fetch, "build") as run:
            self.assertEqual(fetch.install(self.target), folder)
            download.assert_not_called()
            run.assert_called_once()
            fetch.install(self.target, force=True)
            download.assert_called_once()

    def test_a_fresh_target_downloads_then_builds_in_that_order(self):
        order = []
        with mock.patch.object(fetch, "fetch", side_effect=lambda *a: order.append("fetch")), \
                mock.patch.object(fetch, "build", side_effect=lambda *a: order.append("build")):
            fetch.install(self.target)
        self.assertEqual(order, ["fetch", "build"])

    def test_the_setup_log_lands_in_the_target(self):
        folder = self.target / TOP
        folder.mkdir(parents=True)
        (folder / "amd_fidelityfx_dx12.dll").write_bytes(b"MZ")
        (folder / "helixsr-setup.ps1").write_text("#", encoding="utf-8")
        with mock.patch.object(fetch, "fetch"), mock.patch.object(fetch, "build") as run:
            fetch.install(self.target)
        self.assertEqual(run.call_args[0][4], self.target / "helixsr-setup.log")

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
