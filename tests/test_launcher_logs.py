"""Collect logs (Log page): one zip a player can send after a crash, with the game logs, the settings
and what Windows recorded about the PC and the crash. Run: python -m pytest -q tests/test_launcher_logs.py"""
import base64
import os
import queue
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

if sys.platform == 'win32':
    import bbport_launcher_win as launcher


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class CollectLogsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / 'user').mkdir()
        (self.dir / 'user' / 'last_run.log').write_bytes(b'the crashed run\n')
        (self.dir / 'user' / 'previous_run.log').write_bytes(b'the run before\n')
        (self.dir / 'bbport.ini').write_bytes(b'upscaler=fsr3\n')
        (self.dir / 'settings.json').write_bytes(b'{"frame_cap": 60}')
        self.report = subprocess.CompletedProcess([], 0, 'GPU: NVIDIA GeForce RTX 3060, driver 32.0.15.6094\n', '')

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, report=None):
        with mock.patch.object(launcher.subprocess, 'run', return_value=report or self.report) as run, \
                mock.patch.object(launcher, 'ini_path', return_value=self.dir / 'bbport.ini'), \
                mock.patch.object(launcher, 'CONFIG_FILE', self.dir / 'settings.json'):
            path = launcher.collect_logs(self.dir / 'user', self.dir)
        return path, run

    def test_the_zip_holds_both_game_logs_the_settings_and_the_windows_report(self):
        path, run = self.collect()
        self.assertEqual(path.parent, self.dir)
        self.assertTrue(path.name.startswith('bloodborne-logs-') and path.suffix == '.zip', path.name)
        with zipfile.ZipFile(path) as bundle:
            self.assertEqual(sorted(bundle.namelist()),
                             ['bbport.ini', 'last_run.log', 'previous_run.log', 'settings.json', 'system.txt'])
            self.assertEqual(bundle.read('last_run.log'), b'the crashed run\n')
            self.assertEqual(bundle.read('previous_run.log'), b'the run before\n')
            self.assertEqual(bundle.read('bbport.ini'), b'upscaler=fsr3\n')
            system = bundle.read('system.txt').decode('utf-8')
        self.assertIn(f'Launcher {launcher.VERSION}', system)
        self.assertIn('GPU: NVIDIA GeForce RTX 3060, driver 32.0.15.6094', system)
        self.assertEqual(run.call_args.args[0][0], 'powershell')

    def test_the_report_asks_windows_for_the_crash_records_of_the_game_and_driver_resets(self):
        _path, run = self.collect()
        command = run.call_args.args[0]
        self.assertEqual(command[-2], '-EncodedCommand')
        script = base64.b64decode(command[-1]).decode('utf-16-le')
        for needed in ('Win32_VideoController', 'Application Error', 'bb-probe', '4101', 'nvlddmkm', 'amdkmdag'):
            self.assertIn(needed, script)

    def test_missing_files_and_a_failed_report_still_give_a_zip(self):
        for name in ('user/last_run.log', 'user/previous_run.log', 'bbport.ini', 'settings.json'):
            (self.dir / name).unlink()
        path, _run = self.collect(subprocess.CompletedProcess([], 1, '', 'powershell: access denied'))
        with zipfile.ZipFile(path) as bundle:
            self.assertEqual(bundle.namelist(), ['system.txt'])
            system = bundle.read('system.txt').decode('utf-8')
        self.assertIn('powershell: access denied', system)
        self.assertIn('last_run.log: missing', system)

    def test_powershell_that_cannot_start_is_written_in_the_report(self):
        with mock.patch.object(launcher.subprocess, 'run', side_effect=OSError('no powershell')), \
                mock.patch.object(launcher, 'ini_path', return_value=self.dir / 'bbport.ini'), \
                mock.patch.object(launcher, 'CONFIG_FILE', self.dir / 'settings.json'):
            path = launcher.collect_logs(self.dir / 'user', self.dir)
        with zipfile.ZipFile(path) as bundle:
            self.assertIn('no powershell', bundle.read('system.txt').decode('utf-8'))

    def test_a_new_run_keeps_the_last_log_as_the_previous_one(self):
        log = self.dir / 'user' / 'last_run.log'
        process = launcher.start_game([sys.executable, '-c', 'print("new run")'], dict(os.environ), log)
        process.wait()
        self.assertEqual((self.dir / 'user' / 'previous_run.log').read_bytes(), b'the crashed run\n')
        self.assertIn('new run', log.read_text(encoding='utf-8'))

    def written_by_the_button(self, collect):
        """What the Log page shows after Collect logs, with COLLECT standing in for collect_logs()."""
        shown, calls = [], queue.Queue()
        window = types.SimpleNamespace(append=shown.append, ui_calls=calls)
        window.logs_saved = lambda path: launcher.Launcher.logs_saved(window, path)
        with mock.patch.object(launcher, 'collect_logs', collect), mock.patch.object(launcher.subprocess, 'Popen') as popen:
            launcher.Launcher.write_logs(window, self.dir / 'user')
            while not calls.empty():
                calls.get_nowait()()
        return ''.join(shown), popen

    def test_the_button_names_the_zip_and_selects_it_in_explorer(self):
        zip_path = self.dir / 'bloodborne-logs-1.zip'
        shown, popen = self.written_by_the_button(lambda _user: zip_path)
        self.assertIn(str(zip_path), shown)
        self.assertEqual(popen.call_args.args[0], ['explorer', '/select,', str(zip_path)])

    def test_the_button_says_why_the_zip_could_not_be_written(self):
        shown, popen = self.written_by_the_button(mock.Mock(side_effect=PermissionError('the folder is read-only')))
        self.assertIn('the folder is read-only', shown)
        popen.assert_not_called()

    @unittest.skipUnless(os.environ.get('GITHUB_ACTIONS'), 'real Windows only (Wine has no PowerShell)')
    def test_real_windows_answers_with_the_graphics_card_and_windows_version(self):
        path = launcher.collect_logs(self.dir / 'user', self.dir)
        with zipfile.ZipFile(path) as bundle:
            system = bundle.read('system.txt').decode('utf-8')
        self.assertIn('Windows: ', system)
        self.assertIn('GPU: ', system)
        self.assertIn('RAM: ', system)


if __name__ == '__main__':
    unittest.main()
