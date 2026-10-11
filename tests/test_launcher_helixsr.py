"""The launcher's HelixSR rules: where its folder is found, when the entry can be picked and why not, what the
game is told, and what happens to a saved choice that cannot run any more."""
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


def install(folder, built=True):
    """A HelixSR folder: the DLL, and the file its setup writes beside it."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / DLL).write_bytes(b'MZ')
    if built:
        (folder / BUILT).write_text('{}', encoding='utf-8')
    return folder


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.port, self.fallback = self.root / 'port', self.root / 'C_HelixSR'
        self.port.mkdir()
        for name, value in (('PORT_DIR', self.port), ('HELIXSR_FALLBACK_DIR', self.fallback)):
            patcher = mock.patch.object(launcher, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_chosen_folder_itself(self):
        folder = install(self.root / 'mine')
        self.assertEqual(launcher.helixsr_folder(str(folder)), folder)

    def test_the_release_zip_extracts_to_a_versioned_subfolder(self):
        inner = install(self.root / 'mine' / 'HelixSR-1.8.1')
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), inner)

    def test_only_one_level_down_is_searched(self):
        install(self.root / 'mine' / 'extra' / 'HelixSR-1.8.1')
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'mine')))
        install(self.root / 'mine2' / 'other')
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'mine2')))

    def test_the_newest_versioned_subfolder_wins(self):
        install(self.root / 'mine' / 'HelixSR-1.8.1')
        newer = install(self.root / 'mine' / 'HelixSR-1.9.0')
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'mine')), newer)

    def test_a_file_named_like_a_release_is_not_a_folder(self):
        (self.root / 'mine').mkdir()
        (self.root / 'mine' / 'HelixSR-1.8.1').write_text('x', encoding='utf-8')
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'mine')))

    def test_nothing_installed(self):
        self.assertIsNone(launcher.helixsr_folder(''))
        self.assertIsNone(launcher.helixsr_folder(str(self.root / 'does-not-exist')))

    def test_the_port_folder_then_c_helixsr(self):
        fallback = install(self.fallback)
        self.assertEqual(launcher.helixsr_folder(''), fallback)
        beside = install(self.port / 'helixsr')
        self.assertEqual(launcher.helixsr_folder(''), beside)
        self.assertEqual(launcher.helixsr_folder('   '), beside)

    def test_the_port_folder_accepts_the_extracted_zip_too(self):
        inner = install(self.port / 'helixsr' / 'HelixSR-1.8.1')
        self.assertEqual(launcher.helixsr_folder(''), inner)

    def test_the_setting_wins_and_a_wrong_one_falls_through(self):
        mine, beside = install(self.root / 'mine'), install(self.port / 'helixsr')
        self.assertEqual(launcher.helixsr_folder(str(mine)), mine)
        self.assertEqual(launcher.helixsr_folder(str(self.root / 'empty')), beside)

    def test_the_folder_is_inside_the_ignored_and_kept_names(self):
        self.assertIn('helixsr', launcher.USER_FILES)
        ignore = (Path(__file__).resolve().parents[1] / '.gitignore').read_text(encoding='utf-8').split()
        self.assertTrue({'/helixsr/', 'helixsr/'} & set(ignore))


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.built = install(Path(self.tmp.name) / 'built')
        self.unbuilt = install(Path(self.tmp.name) / 'unbuilt', built=False)

    def test_the_gpu_that_cannot_share_images_keeps_its_reason(self):
        for experimental in (False, True):
            for folder in (None, self.built):
                self.assertEqual(launcher.helixsr_state(NO_SHARING, experimental, folder),
                                 ('no', 'the Vulkan driver cannot share images with Direct3D 12'))

    def test_off_by_default_so_the_entry_says_to_turn_experimental_features_on(self):
        for support in (SUPPORTED, None):
            for folder in (None, self.unbuilt, self.built):
                self.assertEqual(launcher.helixsr_state(support, False, folder),
                                 ('no', launcher.FSR4VK_EXPERIMENTAL_REASON))

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
                       launcher.HELIXSR_GPU_REASON, launcher.FSR4VK_EXPERIMENTAL_REASON):
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
                         ('fsr3', launcher.FSR4VK_EXPERIMENTAL_REASON))

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
        for name, value in (('PORT_DIR', self.root / 'port'), ('HELIXSR_FALLBACK_DIR', self.root / 'none')):
            patcher = mock.patch.object(launcher, name, value)
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

    def test_experimental_off_keeps_helixsr_from_loading(self):
        folder = install(self.root / 'mine')
        env = self.environment(False, str(folder))
        self.assertEqual(env['BB_HELIXSR'], '0')
        self.assertEqual(env['BB_HELIXSR_DIR'], str(folder))

    def test_no_folder_sets_no_directory(self):
        env = self.environment(True)
        self.assertNotIn('BB_HELIXSR_DIR', env)
        self.assertNotIn('BB_HELIXSR', env)

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


if __name__ == '__main__':
    unittest.main()
