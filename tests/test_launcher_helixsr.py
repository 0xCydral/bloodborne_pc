"""The launcher's HelixSR rules: where its folder is found, when the entry can be picked and why not, what the
game is told, and what happens to a saved choice that cannot run any more."""
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

if sys.platform == 'win32':
    import bbport_launcher_win as launcher

DLL = 'amd_fidelityfx_dx12.dll'
BUILT = 'helixsr_setup.json'
SUPPORTED = {'helixsr': (True, ''), 'dlss': (False, 'not an NVIDIA RTX GPU')}
NO_SHARING = {'helixsr': (False, 'the Vulkan driver cannot share images with Direct3D 12'), 'dlss': (True, '')}


def install(folder, built=True, nested=False):
    """A HelixSR release folder and the folder helixsr_folder() answers with (the one that holds the DLL).
    1.9.x: both are `folder`, with the setup's file beside the DLL. 2.0.0 (nested): the DLL is in
    `folder`\\HelixSR-fsrfg and the setup's file is in `folder`."""
    dll = folder / 'HelixSR-fsrfg' if nested else folder
    dll.mkdir(parents=True, exist_ok=True)
    (dll / DLL).write_bytes(b'MZ')
    if built:
        (folder / BUILT).write_text('{}', encoding='utf-8')
    return dll


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.port = self.root / 'port'
        self.port.mkdir()
        patcher = mock.patch.object(launcher, 'PORT_DIR', self.port)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_chosen_folder_itself(self):
        folder = install(self.root / 'mine')
        self.assertEqual(launcher.helixsr_folder(str(folder)), folder)

    def test_the_release_zip_extracts_to_a_versioned_subfolder(self):
        inner = install(self.root / 'mine' / 'HelixSR-1.8.1')
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), inner)

    def test_2_0_keeps_the_dll_in_the_fsrfg_folder_of_the_release(self):
        inner = install(self.root / 'mine' / 'HelixSR-2.0.0', nested=True)
        self.assertEqual(inner.name, 'HelixSR-fsrfg')
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), inner)
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine' / 'HelixSR-2.0.0')), inner)  # the release folder
        self.assertEqual(launcher.helixsr_folder(str(inner)), inner)  # or the DLL's own folder

    def test_2_0_unpacked_into_the_port_folder_is_found(self):
        inner = install(self.port / 'helixsr' / 'HelixSR-2.0.0', nested=True)
        self.assertEqual(launcher.helixsr_folder(''), inner)

    def test_2_0_wins_over_a_1_9_copy_next_to_it(self):
        install(self.root / 'mine' / 'HelixSR-1.9.1')
        newest = install(self.root / 'mine' / 'HelixSR-2.0.0', nested=True)
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), newest)

    def test_a_1_9_copy_is_found_when_2_0_is_not_there(self):
        old = install(self.root / 'mine' / 'HelixSR-1.9.1')
        (self.root / 'mine' / 'HelixSR-2.0.0' / 'HelixSR-fsrfg').mkdir(parents=True)  # a release folder without the DLL
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), old)

    def test_only_one_level_down_is_searched(self):
        install(self.root / 'mine' / 'extra' / 'HelixSR-1.8.1')
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'mine')))
        install(self.root / 'mine2' / 'other')
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'mine2')))

    def test_the_newest_versioned_subfolder_wins(self):
        install(self.root / 'mine' / 'HelixSR-1.8.1')
        newer = install(self.root / 'mine' / 'HelixSR-1.9.0')
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), newer)

    def test_versions_compare_as_numbers_not_as_text(self):
        install(self.root / 'mine' / 'HelixSR-1.9.0')
        newest = install(self.root / 'mine' / 'HelixSR-1.10.0')
        install(self.root / 'mine' / 'HelixSR-1.8.1')
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), newest)

    def test_a_name_without_a_version_sorts_below_every_version(self):
        install(self.root / 'mine' / 'HelixSR-backup')
        install(self.root / 'mine' / 'HelixSR-old-copy')
        only = launcher.helixsr_folder(str(self.root / 'mine'))
        self.assertEqual(only.name, 'HelixSR-old-copy')  # no version anywhere: the name decides, but it is found
        versioned = install(self.root / 'mine' / 'HelixSR-0.1')
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), versioned)
        keys = [launcher.helixsr_release_order(Path(name)) for name in ('HelixSR-x', 'HelixSR-1.9.0', 'HelixSR-1.10.0')]
        self.assertEqual(sorted(keys), [keys[0], keys[1], keys[2]])

    def test_a_file_named_like_a_release_is_not_a_folder(self):
        (self.root / 'mine').mkdir()
        (self.root / 'mine' / 'HelixSR-1.8.1').write_text('x', encoding='utf-8')
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'mine')))

    def test_nothing_installed(self):
        self.assertIsNone(launcher.helixsr_folder(''))
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'does-not-exist')))

    def test_nothing_is_looked_for_outside_the_port_and_the_setting(self):
        # a fixed place such as C:\HelixSR is a DLL-planting spot: any user can create a folder at a drive's root
        self.assertFalse(hasattr(launcher, 'HELIXSR_FALLBACK_DIR'))
        self.assertIsNone(launcher.helixsr_folder(''))

    def test_the_port_folder_is_looked_in_when_no_folder_is_chosen(self):
        beside = install(self.port / 'helixsr')
        self.assertEqual(launcher.helixsr_folder(''), beside)
        self.assertEqual(launcher.helixsr_folder('   '), beside)

    def test_the_port_folder_accepts_the_extracted_zip_too(self):
        inner = install(self.port / 'helixsr' / 'HelixSR-1.8.1')
        self.assertEqual(launcher.helixsr_folder(''), inner)

    def test_a_chosen_folder_is_the_only_one_used(self):
        mine, beside = install(self.root / 'mine'), install(self.port / 'helixsr')
        self.assertEqual(launcher.helixsr_folder(str(mine)), mine)
        # a wrong choice is reported as not installed, never silently replaced by the port's copy
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'empty')))
        self.assertEqual(launcher.helixsr_folder(''), beside)

    def test_the_folder_is_inside_the_ignored_and_kept_names(self):
        self.assertIn('helixsr', launcher.USER_FILES)
        ignore = (Path(__file__).resolve().parents[1] / '.gitignore').read_text(encoding='utf-8').split()
        self.assertTrue({'/helixsr/', 'helixsr/'} & set(ignore))


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class OutdatedTests(unittest.TestCase):
    """helixsr_outdated: a copy the launcher itself set up from another zip (or an older release) is offered as
    Update; a newer copy, and any folder the player chose, are left alone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port = Path(self.tmp.name) / 'port'
        patcher = mock.patch.object(launcher, 'PORT_DIR', self.port)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = self.port / 'helixsr'

    def test_the_pinned_copy_is_current(self):
        folder = install(self.root / 'HelixSR-2.0.0', nested=True)
        launcher.fetch_helixsr.write_package_record(self.root)
        self.assertTrue(launcher.fetch_helixsr.package_current(self.root))
        self.assertFalse(launcher.helixsr_outdated(folder))

    def test_a_copy_from_another_zip_is_outdated(self):
        folder = install(self.root / 'HelixSR-2.0.0', nested=True)  # the same version name as the pinned release
        self.assertTrue(launcher.helixsr_outdated(folder))

    def test_a_1_9_copy_is_the_update_case(self):
        # the releases the pin moved off: their DLL sits in the release folder itself, and they are found, built,
        # and offered as Update
        for version in ('1.9.1', '1.9.0', '1.10.0'):  # 1.10.0 is below 2.0.0 as a number
            with self.subTest(version=version):
                folder = install(self.root / f'HelixSR-{version}')
                launcher.fetch_helixsr.write_package_record(self.root)  # even a current record does not cover it
                self.assertTrue(launcher.helixsr_built(folder))
                self.assertTrue(launcher.helixsr_outdated(folder))

    def test_a_newer_release_is_left_alone(self):
        folder = install(self.root / 'HelixSR-2.1.0', nested=True)
        self.assertFalse(launcher.helixsr_outdated(folder))

    def test_a_folder_the_player_chose_is_left_alone(self):
        folder = install(self.root / 'HelixSR-1.9.0')
        self.assertFalse(launcher.helixsr_outdated(folder, str(folder)))

    def test_a_folder_outside_the_launcher_folder_is_left_alone(self):
        self.assertFalse(launcher.helixsr_outdated(install(Path(self.tmp.name) / 'mine')))

    def test_without_the_helper_nothing_is_outdated(self):
        folder = install(self.root / 'HelixSR-1.9.0')
        with mock.patch.object(launcher, 'fetch_helixsr', None):
            self.assertFalse(launcher.helixsr_outdated(folder))


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.built = install(Path(self.tmp.name) / 'built')
        self.unbuilt = install(Path(self.tmp.name) / 'unbuilt', built=False)
        self.built_2 = install(Path(self.tmp.name) / 'HelixSR-2.0.0', nested=True)  # the record is one folder up
        self.unbuilt_2 = install(Path(self.tmp.name) / 'unbuilt-2', built=False, nested=True)

    def test_2_0_is_built_by_the_record_one_folder_above_the_dll(self):
        self.assertEqual(launcher.helixsr_state(SUPPORTED, True, self.built_2), ('ok', ''))
        self.assertEqual(launcher.helixsr_state(SUPPORTED, True, self.unbuilt_2), ('no', launcher.HELIXSR_UNBUILT_REASON))

    def test_the_gpu_that_cannot_share_images_keeps_its_reason(self):
        for experimental in (False, True):
            for folder in (None, self.built):
                self.assertEqual(launcher.helixsr_state(NO_SHARING, experimental, folder),
                                 ('no', 'the Vulkan driver cannot share images with Direct3D 12'))

    def test_off_by_default_so_the_entry_says_to_turn_experimental_features_on(self):
        for support in (SUPPORTED, None):
            for folder in (None, self.unbuilt, self.built):
                self.assertEqual(launcher.helixsr_state(support, False, folder),
                                 ('no', launcher.EXPERIMENTAL_REASON))

    def test_on_without_a_folder_says_where_to_get_it(self):
        state, reason = launcher.helixsr_state(SUPPORTED, True, None)
        self.assertEqual((state, reason), ('no', launcher.HELIXSR_MISSING_REASON))
        self.assertIn('github.com/lonewolf0622/HelixSR', reason)
        self.assertIn('Advanced', reason)

    def test_the_dll_without_its_built_network_says_to_run_the_setup(self):
        state, reason = launcher.helixsr_state(SUPPORTED, True, self.unbuilt)
        self.assertEqual((state, reason), ('no', launcher.HELIXSR_UNBUILT_REASON))
        self.assertIn('helixsr-setup.bat', reason)

    def test_built_and_on_can_be_picked(self):
        self.assertEqual(launcher.helixsr_state(SUPPORTED, True, self.built), ('ok', ''))
        self.assertEqual(launcher.helixsr_state(None, True, self.built), ('ok', ''))  # the GPU check could not run

    def test_upscaler_state_routes_helixsr_and_leaves_the_others_alone(self):
        self.assertEqual(launcher.upscaler_state('helixsr', SUPPORTED, False, True, False, self.built), ('ok', ''))
        self.assertEqual(launcher.upscaler_state('helixsr', SUPPORTED, False, True, True, None),
                         ('no', launcher.HELIXSR_MISSING_REASON))
        self.assertEqual(launcher.upscaler_state('fsr3', SUPPORTED, False), ('ok', ''))
        self.assertEqual(launcher.upscaler_state('dlss', SUPPORTED, False), ('no', 'not an NVIDIA RTX GPU'))
        self.assertEqual(launcher.upscaler_state('fsr411', {'fsr411': (True, '')}, False, True, True), ('ok', ''))

    def test_every_reason_has_a_russian_text(self):
        for reason in (launcher.HELIXSR_MISSING_REASON, launcher.HELIXSR_UNBUILT_REASON,
                       launcher.HELIXSR_GPU_REASON, launcher.EXPERIMENTAL_REASON):
            self.assertIn(reason, launcher.UPSCALER_REASONS_RU)

    def test_the_dropdown_lists_it_after_fsr411_marked_experimental(self):
        names = [name for name, _texts in launcher.UPSCALERS]
        self.assertEqual(names.index('helixsr'), names.index('fsr411') + 1)
        self.assertEqual(dict(launcher.UPSCALERS)['helixsr'][0], 'HelixSR (experimental)')


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.built = install(Path(self.tmp.name))

    def test_a_saved_helixsr_without_experimental_features_falls_back(self):
        self.assertEqual(launcher.upscaler_fallback('helixsr', SUPPORTED, False, True, self.built),
                         ('fsr3', launcher.EXPERIMENTAL_REASON))

    def test_a_saved_helixsr_whose_folder_is_gone_falls_back(self):
        self.assertEqual(launcher.upscaler_fallback('helixsr', SUPPORTED, True, True, None),
                         ('fsr3', launcher.HELIXSR_MISSING_REASON))

    def test_an_rtx_gpu_falls_back_to_dlss(self):
        self.assertEqual(launcher.upscaler_fallback('helixsr', NO_SHARING, True, True, self.built)[0], 'dlss')

    def test_a_usable_choice_stays(self):
        self.assertIsNone(launcher.upscaler_fallback('helixsr', SUPPORTED, True, False, self.built))
        self.assertIsNone(launcher.upscaler_fallback('fsr3', SUPPORTED, False, False))
        self.assertIsNone(launcher.upscaler_fallback('fsr411', {'fsr411': (True, '')}, True, True))


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(launcher, 'PORT_DIR', self.root / 'port')
        patcher.start()
        self.addCleanup(patcher.stop)

    def environment(self, experimental, helixsr_dir='', **extra):
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'experimental': experimental,
                    'helixsr_dir': helixsr_dir, **extra}
        with mock.patch.dict(launcher.os.environ), mock.patch.object(launcher, 'load_ini', return_value=({}, [])):
            for name in ('BB_HELIXSR', 'BB_HELIXSR_DIR'):
                launcher.os.environ.pop(name, None)
            return launcher.game_environment(settings)

    def test_the_game_is_told_the_folder_that_was_found(self):
        folder = install(self.root / 'mine')
        env = self.environment(True, str(folder))
        self.assertEqual(env['BB_HELIXSR_DIR'], str(folder))
        self.assertNotIn('BB_HELIXSR', env)

    def test_the_nested_release_folder_is_what_it_is_told(self):
        inner = install(self.root / 'mine' / 'HelixSR-1.8.1')
        self.assertEqual(self.environment(True, str(self.root / 'mine'))['BB_HELIXSR_DIR'], str(inner))

    def test_2_0_is_told_the_fsrfg_folder_that_holds_the_dll(self):
        inner = install(self.root / 'mine' / 'HelixSR-2.0.0', nested=True)
        env = self.environment(True, str(self.root / 'mine'))
        self.assertEqual(env['BB_HELIXSR_DIR'], str(inner))
        self.assertTrue(env['BB_HELIXSR_DIR'].endswith('HelixSR-fsrfg'))

    def test_experimental_off_keeps_helixsr_from_loading(self):
        folder = install(self.root / 'mine')
        env = self.environment(False, str(folder))
        self.assertEqual(env['BB_HELIXSR'], '0')
        self.assertEqual(env['BB_HELIXSR_DIR'], str(folder))

    def test_no_folder_sets_no_directory(self):
        env = self.environment(True)
        self.assertNotIn('BB_HELIXSR_DIR', env)
        self.assertNotIn('BB_HELIXSR', env)

    def test_an_inherited_directory_does_not_outlive_the_folder(self):
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'experimental': True}
        with mock.patch.dict(launcher.os.environ, {'BB_HELIXSR_DIR': str(self.root / 'planted')}), \
                mock.patch.object(launcher, 'load_ini', return_value=({}, [])):
            self.assertNotIn('BB_HELIXSR_DIR', launcher.game_environment(settings))

    def test_a_wrong_chosen_folder_reaches_the_game(self):
        wrong = str(self.root / 'not-helixsr')
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'experimental': True, 'helixsr_dir': wrong}
        with mock.patch.object(launcher, 'load_ini', return_value=({}, [])):
            self.assertEqual(launcher.game_environment(settings)['BB_HELIXSR_DIR'], wrong)

    def test_an_inherited_zero_does_not_outlive_the_switch(self):
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'experimental': True}
        with mock.patch.dict(launcher.os.environ, {'BB_HELIXSR': '0'}), \
                mock.patch.object(launcher, 'load_ini', return_value=({}, [])):
            self.assertNotIn('BB_HELIXSR', launcher.game_environment(settings))

    def test_extra_variables_still_win(self):
        self.assertEqual(self.environment(False, extra_env='BB_HELIXSR=1')['BB_HELIXSR'], '1')

    def test_the_setting_has_a_default_and_the_fsr4vk_variables_are_unchanged(self):
        self.assertEqual(launcher.APP_DEFAULTS['helixsr_dir'], '')
        self.assertEqual(self.environment(False)['BB_FSR4VK'], '0')


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class CapabilityRowTests(unittest.TestCase):
    def test_the_row_parses(self):
        output = ('UPSCALER fsr3 supported\n'
                  'UPSCALER helixsr supported\n')
        self.assertEqual(launcher.parse_upscaler_support(output)['helixsr'], (True, ''))
        output = f'UPSCALER helixsr unsupported: {launcher.HELIXSR_GPU_REASON}\r\n'
        self.assertEqual(launcher.parse_upscaler_support(output)['helixsr'], (False, launcher.HELIXSR_GPU_REASON))

    def test_the_gpu_reason_is_the_one_the_capability_tool_prints(self):
        source = (Path(__file__).resolve().parents[1] / 'tools' / 'gpu_capabilities.c').read_text(encoding='utf-8')
        self.assertIn(f'fail(&helixsr, "{launcher.HELIXSR_GPU_REASON}");', source)
        self.assertIn('{"helixsr", &helixsr}', source)

    def test_the_renderer_says_the_same_things_as_the_launcher(self):
        # the game's menu and the launcher's dropdown must give the same reason for the same cause
        renderer = Path(__file__).resolve().parents[1] / 'gpu' / 'shadps4' / 'video_core' / 'renderer_vulkan'
        upscaler = (renderer / 'vk_temporal_upscaler.cpp').read_text(encoding='utf-8')
        helixsr = (renderer / 'vk_helixsr.cpp').read_text(encoding='utf-8')
        self.assertIn(f'"{launcher.HELIXSR_GPU_REASON}"', upscaler)
        self.assertIn(f'"{launcher.HELIXSR_UNBUILT_REASON}"', helixsr)


class Var:
    """A tk variable's get/set."""

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class InstallFunctionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port = Path(self.tmp.name) / 'port'
        patcher = mock.patch.object(launcher, 'PORT_DIR', self.port)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_declined_runs_nothing(self):
        with mock.patch.object(launcher.fetch_helixsr, 'install') as install:
            self.assertIsNone(launcher.install_helixsr(False))
        install.assert_not_called()

    def test_consented_installs_into_the_port_helixsr_folder(self):
        lines, steps = [], []
        with mock.patch.object(launcher.fetch_helixsr, 'install') as install:
            self.assertEqual(launcher.install_helixsr(True, steps.append, lines.append, True), '')
        args = install.call_args[0]
        self.assertEqual(args[0], self.port / 'helixsr')
        self.assertEqual((args[1], args[2], args[4], args[5]), (steps.append, lines.append, launcher.NO_WINDOW, True))

    def test_failures_come_back_as_their_reason(self):
        for error in (launcher.fetch_helixsr.FetchError('the zip is damaged'), OSError('disk full')):
            with mock.patch.object(launcher.fetch_helixsr, 'install', side_effect=error):
                self.assertEqual(launcher.install_helixsr(True), str(error))

    def test_a_build_without_the_helper_says_so(self):
        with mock.patch.object(launcher, 'fetch_helixsr', None), mock.patch.object(launcher, 'FETCH_HELIXSR_ERROR', 'boom'):
            self.assertIn('boom', launcher.install_helixsr(True))
            self.assertIn('boom', launcher.helixsr_install_problem())

    def test_built_means_the_setup_wrote_its_file(self):
        self.assertFalse(launcher.helixsr_built(None))
        self.assertFalse(launcher.helixsr_built(install(self.port / 'a', built=False)))
        self.assertTrue(launcher.helixsr_built(install(self.port / 'b')))
        self.assertFalse(launcher.helixsr_built(install(self.port / 'c', built=False, nested=True)))
        self.assertTrue(launcher.helixsr_built(install(self.port / 'd', nested=True)))  # the file is one folder up

    def test_the_consent_names_the_license_the_dll_and_the_downloads_in_both_languages(self):
        for language in ('en', 'ru'):
            with mock.patch.object(launcher, 'LANG', language):
                text = launcher.helixsr_consent_text()
            for part in ('https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt', '310.7.0', '131', '70',
                         '%LOCALAPPDATA%\\HelixSR'):
                self.assertIn(part, text)
            self.assertNotIn('400', text)  # -NoFg: nothing downloads NVIDIA's files for frame generation
        self.assertRegex(text, '[а-я]')  # the last one was Russian


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class InstallButtonTests(unittest.TestCase):
    """The window's methods run on a stand-in for self: no Tk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port = Path(self.tmp.name) / 'port'
        patcher = mock.patch.object(launcher, 'PORT_DIR', self.port)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vars = {'experimental': Var(False), 'helixsr_dir': Var(''), 'upscaler': Var('fsr3')}
        self.window = mock.MagicMock()
        self.window.process = None
        self.window.installing_helixsr = False
        self.window.var = lambda key, _store: self.vars[key]
        self.window.upscaler_states.return_value = {'helixsr': ('ok', '')}
        self.window.ui_calls = queue.Queue()

    def test_declining_the_dialog_starts_nothing(self):
        self.window.messagebox.askyesno.return_value = False
        with mock.patch.object(launcher.threading, 'Thread') as thread, \
                mock.patch.object(launcher, 'install_helixsr') as install:
            launcher.Launcher.start_helixsr_install(self.window)
        self.window.messagebox.askyesno.assert_called_once()
        self.assertIn('NVIDIA', self.window.messagebox.askyesno.call_args[0][1])
        thread.assert_not_called()
        install.assert_not_called()
        self.assertFalse(self.window.installing_helixsr)

    def test_a_running_game_blocks_it_before_any_question(self):
        self.window.process = object()
        launcher.Launcher.start_helixsr_install(self.window)
        self.window.messagebox.askyesno.assert_not_called()
        self.window.messagebox.showinfo.assert_called_once()

    def start(self, built):
        """Presses the button (answered Yes) with the worker run inline; returns install_helixsr's mock."""
        if built:
            install(self.port / 'helixsr')
        self.window.messagebox.askyesno.return_value = True

        class Inline:  # runs the worker where it is started
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()
        with mock.patch.object(launcher.threading, 'Thread', Inline),                 mock.patch.object(launcher, 'install_helixsr', return_value='') as run:
            launcher.Launcher.start_helixsr_install(self.window)
        return run

    def test_yes_runs_the_install_off_the_window_thread_and_reports_back(self):
        run = self.start(built=False)
        self.assertTrue(run.call_args[0][0])  # consented
        self.assertFalse(run.call_args[0][3])  # a first install does not force a download
        self.assertTrue(self.window.installing_helixsr)  # until the result is handled on the Tk thread
        self.window.ui_calls.get_nowait()()
        self.window.helixsr_install_done.assert_called_once_with('')

    def test_reinstall_forces_a_fresh_download(self):
        self.assertTrue(self.start(built=True).call_args[0][3])

    def test_success_turns_experimental_on_and_selects_helixsr(self):
        install(self.port / 'helixsr')
        launcher.Launcher.helixsr_install_done(self.window, '')
        self.assertTrue(self.vars['experimental'].get())
        self.assertEqual(self.vars['upscaler'].get(), 'helixsr')
        text = self.window.status.configure.call_args.kwargs['text']
        self.assertIn('Experimental features turned on', text)
        self.window.apply_upscaler_support.assert_called()
        self.assertFalse(self.window.installing_helixsr)

    def test_success_leaves_experimental_alone_when_it_was_on(self):
        install(self.port / 'helixsr')
        self.vars['experimental'].set(True)
        launcher.Launcher.helixsr_install_done(self.window, '')
        self.assertNotIn('Experimental', self.window.status.configure.call_args.kwargs['text'])

    def test_a_chosen_folder_without_a_built_helixsr_gives_way_to_the_new_copy(self):
        install(self.port / 'helixsr')
        self.vars['helixsr_dir'].set(str(Path(self.tmp.name) / 'elsewhere'))
        launcher.Launcher.helixsr_install_done(self.window, '')
        self.assertEqual(self.vars['helixsr_dir'].get(), '')
        keep = install(Path(self.tmp.name) / 'mine')
        self.vars['helixsr_dir'].set(str(keep))
        launcher.Launcher.helixsr_install_done(self.window, '')
        self.assertEqual(self.vars['helixsr_dir'].get(), str(keep))

    def test_a_gpu_that_cannot_run_it_keeps_the_upscaler_and_says_why(self):
        install(self.port / 'helixsr')
        self.window.upscaler_states.return_value = {'helixsr': ('no', launcher.HELIXSR_GPU_REASON)}
        launcher.Launcher.helixsr_install_done(self.window, '')
        self.assertEqual(self.vars['upscaler'].get(), 'fsr3')
        self.assertIn('Direct3D 12', self.window.status.configure.call_args.kwargs['text'])

    def test_failure_shows_the_reason_and_changes_no_setting(self):
        self.window.installing_helixsr = True
        launcher.Launcher.helixsr_install_done(self.window, 'download failed: HTTP 404')
        self.assertEqual({k: v.get() for k, v in self.vars.items()},
                         {'experimental': False, 'helixsr_dir': '', 'upscaler': 'fsr3'})
        self.assertFalse(self.window.installing_helixsr)
        self.assertIn('HTTP 404', self.window.messagebox.showerror.call_args[0][1])
        self.window.apply_upscaler_support.assert_not_called()

    def test_the_button_reads_reinstall_once_a_built_copy_exists(self):
        self.window.helixsr_button = mock.MagicMock()
        launcher.Launcher.refresh_helixsr_install(self.window)
        self.assertEqual(self.window.helixsr_button.configure.call_args.kwargs['text'], 'Install HelixSR')
        install(self.port / 'helixsr')
        launcher.Launcher.refresh_helixsr_install(self.window)
        self.assertEqual(self.window.helixsr_button.configure.call_args.kwargs['text'], 'Reinstall HelixSR')
        self.window.installing_helixsr = True
        launcher.Launcher.refresh_helixsr_install(self.window)
        self.assertEqual(self.window.helixsr_button.configure.call_args.kwargs['state'], 'disabled')

    def test_the_button_reads_update_for_a_copy_from_another_zip(self):
        self.window.helixsr_button = mock.MagicMock()
        install(launcher.fetch_helixsr.install_folder(self.port / 'helixsr'), nested=True)  # pinned folder, no record yet
        launcher.Launcher.refresh_helixsr_install(self.window)
        self.assertEqual(self.window.helixsr_button.configure.call_args.kwargs['text'], 'Update HelixSR')
        self.assertIn('Update HelixSR', self.window.helixsr_info.configure.call_args.kwargs['text'])
        launcher.fetch_helixsr.write_package_record(self.port / 'helixsr')  # the pinned zip's own record
        launcher.Launcher.refresh_helixsr_install(self.window)
        self.assertEqual(self.window.helixsr_button.configure.call_args.kwargs['text'], 'Reinstall HelixSR')

    def test_a_1_9_copy_reads_update_not_reinstall(self):
        self.window.helixsr_button = mock.MagicMock()
        install(self.port / 'helixsr' / 'HelixSR-1.9.1')  # built, DLL in the release folder
        launcher.Launcher.refresh_helixsr_install(self.window)
        self.assertEqual(self.window.helixsr_button.configure.call_args.kwargs['text'], 'Update HelixSR')

    def test_updating_a_1_9_copy_downloads_the_pinned_release(self):
        install(self.port / 'helixsr' / 'HelixSR-1.9.1')
        self.assertTrue(self.start(built=False).call_args[0][3])  # force: nothing of the old folder is reused

    def test_an_outdated_copy_is_refetched_not_built_in_place(self):
        folder = self.port / 'helixsr' / 'HelixSR-2.0.0'
        (folder / 'HelixSR-fsrfg').mkdir(parents=True)
        (folder / 'HelixSR-fsrfg' / 'amd_fidelityfx_dx12.dll').write_bytes(b'MZ')
        (folder / 'helixsr-setup.ps1').write_text('#', encoding='utf-8')  # extracted, but never built
        run = self.start(built=False)
        self.assertTrue(run.call_args[0][3])  # force: the files did not come from the pinned zip


if __name__ == '__main__':
    unittest.main()
