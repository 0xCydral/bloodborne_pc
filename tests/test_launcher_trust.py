"""The launcher's HTTPS trust (install_trust): a PC whose certificate store lacks GitHub's root CAs still reaches
GitHub for the update check and the downloads."""
import ssl
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

if sys.platform == 'win32':
    import bbport_launcher_win as launcher

# api.github.com chains to USERTrust ECC, the release downloads to ISRG Root X1 (both measured 2026-10-24).
GITHUB_ROOTS = {'USERTrust ECC Certification Authority', 'ISRG Root X1'}


@unittest.skipUnless(sys.platform == 'win32', 'the launcher is Windows-only')
class InstallTrust(unittest.TestCase):
    def test_urlopen_trusts_github_roots_with_an_empty_windows_store(self):
        installed = []
        # A context with no roots at all: the worst case of a Windows store that never cached GitHub's.
        with mock.patch.object(ssl, 'create_default_context', lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)), \
                mock.patch.object(urllib.request, 'install_opener', installed.append):
            launcher.install_trust()
        (handler,) = [h for h in installed[0].handlers if isinstance(h, urllib.request.HTTPSHandler)]
        names = {dict(pair[0] for pair in cert['subject']).get('commonName') for cert in handler._context.get_ca_certs()}
        self.assertLessEqual(GITHUB_ROOTS, names)


if __name__ == '__main__':
    unittest.main()
