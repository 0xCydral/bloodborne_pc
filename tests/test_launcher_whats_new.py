"""The launcher's release notes: the plain list of what changed (plain_notes), the notes of every newer release
before an update, and the "Updated to" notice after one. Canned GitHub JSON, no network, no window."""
import json
import queue
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

import bbport_lang  # noqa: E402
import bbport_launcher_win as launcher  # noqa: E402

# The body of the real windows-v1.8.0-beta.2 release: long, technical, no "In short" section.
BETA_2_BODY = """A native Windows build of the Bloodborne PS4 port. The download contains no game files; see the [README](https://github.com/0xCydral/bloodborne_pc#readme) for features, requirements and install steps. This is a beta: copy your `user` folder before you try it.

## New in 1.8.0-beta.2

- **Install HelixSR** now installs [HelixSR 1.9.0](https://github.com/lonewolf0622/HelixSR/releases/tag/v1.9.0) by lonewolf0622: sharper images, an experimental int8 network that HelixSR turns on by itself on AMD RDNA 2 and newer, and experimental Radeon Vega support. If you already use HelixSR, press **Reinstall HelixSR** in Advanced; the launcher then uses the newest `HelixSR-*` folder. HelixSR 1.9.0 is under the HelixSR Freeware License, which allows this download of the official, unmodified release.
- After the in-game menu or the name entry closed, every gamepad button stayed ignored until all of them were released, so a button pressed right after closing did nothing. Only the buttons still held from the menu are ignored now.

## What to test

1. Advanced > Performance > **Reinstall HelixSR**, then play a few minutes with HelixSR selected. Say which graphics card you have; AMD cards (RDNA 2 and newer, Vega) are the ones nobody has tried yet.
2. Open the in-game menu with a gamepad, close it, and press a button at once: it should act.

Send `user\\last_run.log` with any report.

## Known issues

- HelixSR 1.9.0 has not been run in this game on an AMD card yet.

## Updating

The launcher offers this version when **Beta versions** is on (Advanced > Launcher). Saves and settings stay. The `helixsr` folder stays too; Reinstall HelixSR adds 1.9.0 next to 1.8.1.

SHA-256 of `bbport-windows.zip`: `a5aae0d63e7632d8d44e16b3afa936994d0028fb8645f025e2dcb177f8ec5235`
""".replace('\n', '\r\n')  # GitHub's API sends CRLF

IN_SHORT_BODY = """A native Windows build of the Bloodborne PS4 port.

## In short

- Game sound works again after you change the output device.
- **HelixSR** installs from the launcher: press [Install HelixSR](https://example.test/help) in Advanced.
- The launcher shows what is new before you update.

## New in 1.9.0

- Fixed the XAudio2 voice pool leaking one voice per cue. This sentence must not appear.
"""


def release(version, body='', prerelease=False, draft=False, package=True):
    tag = f'windows-v{version}'
    return {'tag_name': tag, 'prerelease': prerelease, 'draft': draft, 'body': body,
            'html_url': f'https://example.test/releases/tag/{tag}',
            'assets': [{'name': 'bbport-windows.zip', 'browser_download_url': f'https://example.test/{tag}.zip'}]
            if package else []}


def bullets(*items):
    return '## In short\n\n' + ''.join(f'- {item}\n' for item in items)


class PlainNotesTests(unittest.TestCase):
    def test_an_in_short_section_is_all_that_is_shown(self):
        self.assertEqual(launcher.plain_notes(IN_SHORT_BODY), [
            'Game sound works again after you change the output device.',
            'HelixSR installs from the launcher: press Install HelixSR in Advanced.',
            'The launcher shows what is new before you update.'])

    def test_in_short_bullets_keep_every_sentence(self):
        self.assertEqual(launcher.plain_notes(bullets('Sound works again. Restart the game once.')),
                         ['Sound works again. Restart the game once.'])

    def test_the_heading_may_be_any_level_and_any_case(self):
        for heading in ('# In short', '### IN SHORT', '## In short:'):
            with self.subTest(heading=heading):
                self.assertEqual(launcher.plain_notes(f'{heading}\n- One.\n'), ['One.'])

    def test_a_body_without_in_short_falls_back_to_the_first_sentence_of_each_new_in_bullet(self):
        self.assertEqual(launcher.plain_notes(BETA_2_BODY), [
            'Install HelixSR now installs HelixSR 1.9.0 by lonewolf0622: sharper images, an experimental int8 network '
            'that HelixSR turns on by itself on AMD RDNA 2 and newer, and experimental Radeon Vega support.',
            'After the in-game menu or the name entry closed, every gamepad button stayed ignored until all of them '
            'were released, so a button pressed right after closing did nothing.'])

    def test_the_fallback_skips_testing_and_known_issue_sections(self):
        text = ' '.join(launcher.plain_notes(BETA_2_BODY))
        for left_out in ('Reinstall HelixSR', 'has not been run', 'Saves and settings', 'SHA-256', 'README'):
            self.assertNotIn(left_out, text)

    def test_the_fallback_reads_added_changed_and_fixed_in_order(self):
        body = ('# 1.8.0\n\n### Fixed\n\n- Browse works.\n\n### Added\n\n- A new button. More about it.\n\n'
                '### Changed\n\n- HelixSR 1.9.1 is installed.\n\n### Removed\n\n- Nothing shown.\n')
        self.assertEqual(launcher.plain_notes(body), ['Browse works.', 'A new button.', 'HelixSR 1.9.1 is installed.'])

    def test_an_empty_in_short_falls_back_to_the_rest(self):
        self.assertEqual(launcher.plain_notes('## In short\n\n## New in 2.0\n\n- Works. Really.\n'), ['Works.'])

    def test_a_body_with_nothing_to_list_gives_nothing(self):
        for body in (None, '', 'Just a paragraph.', '## What to test\n\n1. Try it.\n', 7):
            with self.subTest(body=body):
                self.assertEqual(launcher.plain_notes(body), [])

    def test_markdown_is_removed(self):
        text = ('**Bold**, __also bold__, *italic*, ~~gone~~, `code`, [a link](https://example.test/x), '
                '![a picture](https://example.test/p.png) and <https://example.test/auto>.')
        self.assertEqual(launcher.plain_notes(bullets(text)), [
            'Bold, also bold, italic, gone, code, a link, a picture and https://example.test/auto.'])

    def test_markdown_inside_a_link_text_and_snake_case_survive(self):
        self.assertEqual(launcher.plain_notes(bullets('[`BLauncher.exe`](https://example.test) keeps last_run_version')),
                         ['BLauncher.exe keeps last_run_version'])

    def test_numbered_and_star_bullets_count_and_continued_lines_join(self):
        body = '## In short\n\n1. First\n   goes on.\n2) Second\n* Third\n+ Fourth\n'
        self.assertEqual(launcher.plain_notes(body), ['First goes on.', 'Second', 'Third', 'Fourth'])

    def test_a_code_block_is_not_a_list(self):
        self.assertEqual(launcher.plain_notes('## In short\n\n- Real.\n\n```\n- not a bullet\n## not a heading\n```\n'),
                         ['Real.'])

    def test_at_most_six_items(self):
        listed = [f'Item {number}.' for number in range(1, 10)]
        self.assertEqual(launcher.plain_notes(bullets(*listed)), listed[:6])
        fallback = '## New in 2.0\n\n' + ''.join(f'- Change {number}.\n' for number in range(1, 10))
        self.assertEqual(len(launcher.plain_notes(fallback)), 6)
        self.assertEqual(len(launcher.plain_notes(bullets(*listed), limit=2)), 2)

    def test_a_long_first_sentence_is_cut_at_a_word(self):
        [item] = launcher.plain_notes('## New in 2.0\n\n- ' + 'word ' * 100 + '\n')
        self.assertLessEqual(len(item), launcher.NOTES_ITEM_CHARS)
        self.assertTrue(item.endswith('word…'))

    def test_a_version_number_does_not_end_a_sentence(self):
        self.assertEqual(launcher.plain_notes('## New in 2.0\n\n- HelixSR 1.9.1 is installed, e.g. the 4.1 one. Next.\n'),
                         ['HelixSR 1.9.1 is installed, e.g. the 4.1 one.'])


class NewestReleaseBodyTests(unittest.TestCase):
    def test_the_newest_release_carries_its_body(self):
        listed = [release('1.8.0-beta.2', BETA_2_BODY, True), release('1.8.0-beta.1', 'old', True)]
        version, url, page, body = launcher.newest_release(listed, beta=True)
        self.assertEqual((version, url, page), ('1.8.0-beta.2', 'https://example.test/windows-v1.8.0-beta.2.zip',
                                                'https://example.test/releases/tag/windows-v1.8.0-beta.2'))
        self.assertEqual(body, BETA_2_BODY)

    def test_a_missing_or_odd_body_is_an_empty_string(self):
        for body in (None, 5, ['text']):
            odd = {**release('1.7.0'), 'body': body}
            self.assertEqual(launcher.newest_release([odd], beta=False)[3], '')
        gone = release('1.7.0')
        del gone['body']
        self.assertEqual(launcher.newest_release([gone], beta=False)[3], '')


LISTED = [release('1.9.0-beta.2', bullets('Beta two.'), True),
          release('1.9.0-beta.1', bullets('Beta one.'), True),
          release('1.8.0', bullets('Stable eight.')),
          release('1.8.0-beta.5', bullets('Beta five.'), True),
          release('1.8.0-beta.4', '## What to test\n1. Try it.\n', True),
          release('1.7.0', bullets('Seven.')),
          release('1.9.0', draft=True),
          release('v0.5-pre2', prerelease=True)]


class NewerNotesTests(unittest.TestCase):
    def versions(self, installed, beta, **options):
        return [entry[0] for entry in launcher.newer_notes(LISTED, beta, installed, **options)]

    def test_every_newer_release_comes_newest_first(self):
        self.assertEqual(self.versions('1.8.0-beta.4', True), ['1.9.0-beta.2', '1.9.0-beta.1', '1.8.0', '1.8.0-beta.5'])

    def test_the_order_of_the_list_does_not_matter(self):
        self.assertEqual([e[0] for e in launcher.newer_notes(list(reversed(LISTED)), True, '1.8.0-beta.4')],
                         self.versions('1.8.0-beta.4', True))

    def test_a_stable_user_does_not_see_the_betas(self):
        self.assertEqual(self.versions('1.7.0', False), ['1.8.0'])

    def test_the_installed_version_and_older_ones_are_left_out(self):
        self.assertEqual(self.versions('1.9.0-beta.2', True), [])
        self.assertEqual(self.versions('1.8.0', True), ['1.9.0-beta.2', '1.9.0-beta.1'])

    def test_drafts_and_other_tags_are_left_out(self):
        self.assertNotIn('1.9.0', self.versions('1.0.0', True))
        self.assertEqual(len(self.versions('1.0.0', True)), 6)

    def test_at_most_five_versions(self):
        self.assertEqual(launcher.NOTES_VERSIONS, 5)
        self.assertEqual(self.versions('1.0.0', True), ['1.9.0-beta.2', '1.9.0-beta.1', '1.8.0', '1.8.0-beta.5',
                                                        '1.8.0-beta.4'][:5])
        self.assertEqual(len(self.versions('1.0.0', True, limit=2)), 2)

    def test_each_entry_is_the_version_its_page_and_its_plain_notes(self):
        entries = launcher.newer_notes(LISTED, True, '1.8.0-beta.5')
        self.assertEqual(entries[0], ('1.9.0-beta.2', 'https://example.test/releases/tag/windows-v1.9.0-beta.2',
                                      ['Beta two.']))
        self.assertEqual(entries[2][2], ['Stable eight.'])

    def test_a_release_without_notes_is_still_listed_with_none(self):
        entry = [e for e in launcher.newer_notes(LISTED, True, '1.8.0-beta.3') if e[0] == '1.8.0-beta.4'][0]
        self.assertEqual(entry[2], [])

    def test_a_list_of_the_wrong_shape_is_an_error(self):
        with self.assertRaises(ValueError):
            launcher.newer_notes({'message': 'API rate limit exceeded'}, True, '1.0.0')

    def test_the_notes_of_the_running_version(self):
        self.assertEqual(launcher.installed_notes(LISTED, '1.8.0-beta.5'),
                         ('1.8.0-beta.5', 'https://example.test/releases/tag/windows-v1.8.0-beta.5', ['Beta five.']))
        self.assertIsNone(launcher.installed_notes(LISTED, '1.2.3'))
        self.assertIsNone(launcher.installed_notes(LISTED, '1.9.0'))  # a draft is not a release


class UpdatedSinceTests(unittest.TestCase):
    def test_only_a_newer_version_than_the_last_one_run_counts(self):
        self.assertTrue(launcher.updated_since('1.8.0-beta.4', '1.8.0-beta.5'))
        self.assertTrue(launcher.updated_since('1.8.0-beta.5', '1.8.0'))
        self.assertFalse(launcher.updated_since('1.8.0-beta.5', '1.8.0-beta.5'))
        self.assertFalse(launcher.updated_since('1.8.0', '1.8.0-beta.5'))  # an older build put back

    def test_a_first_run_has_nothing_to_compare(self):
        for last in ('', None):
            self.assertFalse(launcher.updated_since(last, '1.8.0-beta.5'))


class StartupNoticeTests(unittest.TestCase):
    """Launcher.start_version_check and check_updated on a stand-in window, with a scratch settings file."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name) / 'config' / 'settings.json'
        for patch in (mock.patch.object(launcher, 'CONFIG_FILE', self.config),
                      mock.patch.object(launcher, 'CONFIG_DIR', self.config.parent),
                      mock.patch.object(launcher, 'VERSION', '1.8.0-beta.5')):
            patch.start()
            self.addCleanup(patch.stop)
        self.shown, self.threads = [], []

    def window(self, app):
        window = types.SimpleNamespace(app=app, ui_calls=queue.Queue(), show_updated=self.shown.append)
        window.remember_version = lambda: launcher.Launcher.remember_version(window)
        window.check_updated = lambda: launcher.Launcher.check_updated(window)
        return window

    def start(self, app):
        window = self.window(app)
        with mock.patch.object(launcher.threading, 'Thread') as thread:
            launcher.Launcher.start_version_check(window)
        self.threads = thread.call_args_list
        return window

    def saved(self):
        return json.loads(self.config.read_text(encoding='utf-8')) if self.config.exists() else None

    def drain(self, window):
        while not window.ui_calls.empty():
            window.ui_calls.get_nowait()()

    def test_the_first_run_stores_the_version_and_shows_nothing(self):
        window = self.start({})
        self.assertEqual(window.app['last_run_version'], '1.8.0-beta.5')
        self.assertEqual(self.saved(), {'last_run_version': '1.8.0-beta.5'})
        self.assertEqual((self.threads, self.shown), ([], []))

    def test_storing_the_version_keeps_the_other_saved_settings_and_adds_no_defaults(self):
        self.config.parent.mkdir()
        self.config.write_text(json.dumps({'fullscreen': True, 'last_run_version': ''}), encoding='utf-8')
        self.start({'fullscreen': True})
        self.assertEqual(self.saved(), {'fullscreen': True, 'last_run_version': '1.8.0-beta.5'})

    def test_the_same_version_again_does_nothing(self):
        window = self.start({'last_run_version': '1.8.0-beta.5'})
        self.assertEqual((self.threads, self.shown, self.saved()), ([], [], None))
        self.assertTrue(window.ui_calls.empty())

    def test_an_older_build_put_back_is_stored_without_a_notice(self):
        window = self.start({'last_run_version': '1.9.0'})
        self.assertEqual((window.app['last_run_version'], self.threads), ('1.8.0-beta.5', []))

    def test_a_new_version_looks_for_its_notes_in_the_background(self):
        window = self.start({'last_run_version': '1.8.0-beta.3'})
        self.assertEqual(len(self.threads), 1)
        self.assertTrue(self.threads[0].kwargs['daemon'])
        self.assertEqual(self.threads[0].kwargs['target'], window.check_updated)
        self.assertEqual(window.app['last_run_version'], '1.8.0-beta.3')  # not stored before the notes were shown
        self.assertIsNone(self.saved())

    def test_a_new_version_with_update_checks_off_makes_no_request(self):
        self.start({'last_run_version': '1.8.0-beta.3', 'check_updates': False})
        self.assertEqual(self.threads, [])

    def fetch(self, window, listed):
        with mock.patch.object(launcher, 'fetch_releases', side_effect=listed if isinstance(listed, Exception) else None,
                               return_value=None if isinstance(listed, Exception) else listed) as fetched:
            launcher.Launcher.check_updated(window)
            self.drain(window)
        return fetched

    def test_the_notes_of_the_new_version_are_shown_once_and_the_version_stored(self):
        window = self.window({'last_run_version': '1.8.0-beta.3'})
        self.fetch(window, LISTED)
        self.assertEqual(self.shown, [('1.8.0-beta.5', 'https://example.test/releases/tag/windows-v1.8.0-beta.5',
                                       ['Beta five.'])])
        self.assertEqual(self.saved()['last_run_version'], '1.8.0-beta.5')
        self.assertEqual(window.app['last_run_version'], '1.8.0-beta.5')
        self.assertFalse(launcher.updated_since(window.app['last_run_version'], launcher.VERSION))  # not again

    def test_a_failed_fetch_shows_nothing_and_leaves_the_version_for_the_next_start(self):
        for failure in (OSError('offline'), ValueError('not JSON'), KeyError('tag_name')):
            window = self.window({'last_run_version': '1.8.0-beta.3'})
            self.fetch(window, failure)
            self.assertEqual((self.shown, window.app['last_run_version'], self.saved()), ([], '1.8.0-beta.3', None))

    def test_a_programming_error_is_not_taken_for_a_network_failure(self):
        with self.assertRaises(AttributeError):
            self.fetch(self.window({'last_run_version': '1.8.0-beta.3'}), AttributeError('no attribute'))

    def test_a_version_with_no_notes_shows_nothing_and_is_stored(self):
        for listed in ([release('1.8.0-beta.5', 'Only a paragraph.', True)], [release('1.7.0')]):
            window = self.window({'last_run_version': '1.8.0-beta.3'})
            self.fetch(window, listed)
            self.assertEqual((self.shown, window.app['last_run_version']), ([], '1.8.0-beta.5'))

    def test_the_pre_releases_are_listed_for_the_installed_beta(self):
        window = self.window({'last_run_version': '1.8.0-beta.3'})
        fetched = self.fetch(window, LISTED)
        fetched.assert_called_once_with(True)


class ShowWhatsNewTests(unittest.TestCase):
    """Launcher.show_whats_new on a stand-in window: when the dialog opens and when the page does."""

    NOTES = [('1.9.0', 'https://example.test/nine', ['Nine.']), ('1.8.0', 'https://example.test/eight', [])]

    def show(self, notes):
        window = types.SimpleNamespace(notes_dialog=mock.Mock(), install_update=mock.Mock())
        with mock.patch.object(launcher.webbrowser, 'open') as opened, mock.patch.object(launcher, 'LANG', 'en'):
            launcher.Launcher.show_whats_new(window, '1.9.0', 'https://example.test/nine.zip', 'https://example.test/nine',
                                             notes)
        return window, opened

    def test_notes_open_the_dialog_with_update_now_and_later(self):
        window, opened = self.show(self.NOTES)
        opened.assert_not_called()
        title, notes, page, actions = window.notes_dialog.call_args[0]
        self.assertEqual((title, notes, page), ("What's new in 1.9.0", self.NOTES, 'https://example.test/nine'))
        self.assertEqual([label for label, _command, _kind in actions], ['Update now', 'Later'])
        actions[0][1]()
        window.install_update.assert_called_once_with('1.9.0', 'https://example.test/nine.zip', 'https://example.test/nine')

    def test_no_bullets_anywhere_opens_the_release_page_as_before(self):
        for notes in ([], [('1.9.0', 'https://example.test/nine', [])]):
            window, opened = self.show(notes)
            opened.assert_called_once_with('https://example.test/nine')
            window.notes_dialog.assert_not_called()


class TranslationTests(unittest.TestCase):
    NEW = ("What's new in {}", 'Update now', 'Later', 'Full notes', 'Updated to {}',
           'No summary for this version. See the full notes.')

    def test_every_language_translates_the_new_texts(self):
        for language in bbport_lang.TRANSLATIONS:
            with self.subTest(language=language):
                texts = bbport_lang.table(language)  # raises when a list is out of step with KEYS
                for text in self.NEW:
                    self.assertTrue(texts.get(text), text)
                    self.assertEqual(texts[text].count('{}'), text.count('{}'), text)

    def test_the_texts_are_keys(self):
        for text in self.NEW:
            self.assertEqual(bbport_lang.KEYS.count(text), 1, text)


if __name__ == '__main__':
    unittest.main()
