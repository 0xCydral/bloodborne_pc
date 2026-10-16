"""settings.json as the Windows launcher loads it. Up to 1.8.0-beta.3 the Simple page's Browse button wrote the
chosen game folder into frame_cap, and every start after that failed in simple_cap_line (int() of a path)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

import bbport_launcher_win as launcher  # noqa: E402


class LoadAppSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.config = self.dir / 'settings.json'

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, saved):
        self.config.write_text(json.dumps(saved), encoding='utf-8')
        with mock.patch.object(launcher, 'CONFIG_FILE', self.config):
            return launcher.load_app_settings()

    def game_folder(self, name):
        folder = self.dir / name
        folder.mkdir()
        (folder / 'eboot.bin').write_bytes(b'')
        return str(folder)

    def test_a_game_folder_saved_as_the_frame_cap_goes_back_to_the_game_folder(self):
        picked = self.game_folder('game')
        settings = self.load({'frame_cap': picked, 'game_dir': str(self.dir / 'missing')})
        self.assertEqual(settings['frame_cap'], '')
        self.assertEqual(settings['game_dir'], picked)
        self.assertTrue(launcher.simple_cap_line(settings['frame_cap'], 144))

    def test_a_working_game_folder_is_kept(self):
        kept, picked = self.game_folder('kept'), self.game_folder('picked')
        settings = self.load({'frame_cap': picked, 'game_dir': kept})
        self.assertEqual((settings['frame_cap'], settings['game_dir']), ('', kept))

    def test_any_other_unknown_frame_cap_is_auto(self):
        for bad in ('E:/no/such/folder', 'abc', '75', 12):
            with self.subTest(bad=bad):
                settings = self.load({'frame_cap': bad})
                self.assertEqual(settings['frame_cap'], '')
                self.assertEqual(settings['game_dir'], launcher.APP_DEFAULTS['game_dir'])

    def test_the_caps_the_launcher_offers_are_kept(self):
        for cap in [value for value, _label in launcher.FRAME_CAPS] + ['half']:
            with self.subTest(cap=cap):
                self.assertEqual(self.load({'frame_cap': cap})['frame_cap'], cap)


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class PlayPageBrowseTests(unittest.TestCase):
    """The Play page's Browse button in a real window: the folder it picks is the game folder, never the frame cap
    (the button's lambda used to read `var` after a later loop had rebound it to the frame cap)."""

    def setUp(self):
        import tkinter
        from tkinter import filedialog, messagebox, ttk
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        scratch = Path(self.tmp.name)
        (scratch / 'config').mkdir()
        (scratch / 'config' / 'settings.json').write_text('{"check_updates": false}', encoding='utf-8')
        for patch in (mock.patch.dict(launcher.os.environ, {'BB_CONFIG': str(scratch / 'bbport.ini'),
                                                            'BB_LAUNCHER_ANIMATIONS': '0'}),
                      mock.patch.object(launcher, 'CONFIG_FILE', scratch / 'config' / 'settings.json'),
                      mock.patch.object(launcher, 'CONFIG_DIR', scratch / 'config'),
                      mock.patch.object(launcher, 'DATA_DIR', scratch),
                      mock.patch.object(launcher, 'gpu_tool', return_value=(scratch / 'no-such.exe', {})),
                      mock.patch.object(launcher, 'LANG', 'en')):
            patch.start()
            self.addCleanup(patch.stop)
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError:
            self.skipTest('no display for Tk')
        self.addCleanup(self.root.destroy)
        self.picked = scratch / 'picked game'
        self.picked.mkdir()
        self.buttons, made = [], launcher.FlatButton.__init__

        def record(button, *args, **kwargs):
            made(button, *args, **kwargs)
            self.buttons.append(button)
        with mock.patch.object(launcher.FlatButton, '__init__', record):
            self.app = launcher.Launcher(self.root, tkinter, ttk,
                                         mock.Mock(askdirectory=mock.Mock(return_value=str(self.picked))), messagebox)

    def widgets(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.widgets(child)

    def test_browse_sets_the_game_folder_and_leaves_the_frame_cap_alone(self):
        cap = self.app.var('frame_cap', 'app').get()
        game_entry = next(w for w in self.widgets(self.root)
                          if w.winfo_class() == 'TEntry' and str(w.cget('textvariable')) == str(self.app.vars['game_dir']))
        browse = next(b for b in self.buttons if b.canvas.master is game_entry.master and b.text.startswith('Browse'))
        browse.invoke()
        self.assertEqual(self.app.var('game_dir', 'app').get(), str(self.picked))
        self.assertEqual(self.app.var('frame_cap', 'app').get(), cap)


if __name__ == '__main__':
    unittest.main()
