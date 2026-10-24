"""Installer safety tests; no host services, package managers or credentials used."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'deploy/setup.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
CONFIG = dict(domain='wechat.example.com', team='test-team', aud='a'*64, emails='owner@example.com')


class ValidationTests(unittest.TestCase):
    def test_hostnames(self):
        self.assertEqual(installer.hostname('WeChat.Example.COM.'), CONFIG['domain'])
        for value in ('https://wechat.example.com', 'example.com/x', '127.0.0.1', '::1',
                      'localhost', '-bad.example.com', 'bad_.example.com', 'x\nENV=evil'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                installer.hostname(value)

    def test_team_name_no_url_injection(self):
        self.assertEqual(installer.team_name('https://test-team.cloudflareaccess.com/'), 'test-team')
        for value in ('x.evil.example', 'test-team/path', 'test-team\nX=1', 'test@team'):
            with self.assertRaises(RuntimeError):
                installer.team_name(value)

    def test_exact_email_list(self):
        self.assertEqual(installer.email_list('Owner@Example.com,owner@example.com'), 'owner@example.com')
        for value in ('', '*@example.com', 'Everyone', 'a@example.com\nWECHAT_IMAGE=evil'):
            with self.assertRaises(RuntimeError):
                installer.email_list(value)

    def test_audience(self):
        self.assertEqual(installer.audience('A'*64), 'a'*64)
        for value in ('a'*63, 'not-a-token', 'a'*64+'\nEVIL=1'):
            with self.assertRaises(RuntimeError):
                installer.audience(value)


class EdgeTests(unittest.TestCase):
    def opener(self, location, status=302):
        opener = MagicMock()
        redirect = MagicMock(status=status, headers={'Location': location})
        redirect.__enter__.return_value = redirect
        keys = MagicMock()
        keys.__enter__.return_value = keys
        keys.read.return_value = b'{"keys":[{"kty":"RSA","alg":"RS256"}]}'
        opener.open.side_effect = [redirect]*3 + [keys]
        return opener

    def test_expected_team_gate(self):
        opener = self.opener('https://test-team.cloudflareaccess.com/cdn-cgi/access/login/example')
        with patch.object(installer.urllib.request, 'build_opener', return_value=opener):
            installer.verify_edge(CONFIG)
        self.assertEqual(opener.open.call_count, 4)

    def test_discovery_does_not_prompt_or_follow_redirect(self):
        opener = self.opener('https://test-team.cloudflareaccess.com/cdn-cgi/access/login/example?redirect_url=x')
        with patch.object(installer.urllib.request, 'build_opener', return_value=opener) as build, \
             patch.object(installer, 'ask', side_effect=AssertionError('Unexpected manual team prompt')):
            self.assertEqual(installer.resolve_access_team(CONFIG['domain']), 'test-team')
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.args[0].full_url, 'https://wechat.example.com/')
        self.assertTrue(any(isinstance(arg, installer.NoRedirect) for arg in build.call_args.args))

    def test_discovery_handles_urllib_redirect_exception(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError('https://wechat.example.com/', 302, 'Found',
            {'Location': 'https://test-team.cloudflareaccess.com/cdn-cgi/access/login/example'}, io.BytesIO())
        with patch.object(installer.urllib.request, 'build_opener', return_value=opener), \
             patch.object(installer, 'ask', side_effect=AssertionError('Unexpected manual team prompt')):
            self.assertEqual(installer.resolve_access_team(CONFIG['domain']), 'test-team')

    def test_discovery_failure_offers_validated_manual_input(self):
        for failure in (RuntimeError('no login redirect'), urllib.error.URLError('network unavailable')):
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(installer, 'edge_response', side_effect=failure), \
                 patch.object(installer, 'ask', return_value='https://backup-team.cloudflareaccess.com') as ask:
                self.assertEqual(installer.resolve_access_team(CONFIG['domain']), 'backup-team')
                ask.assert_called_once()

    def test_discovery_rejects_unsafe_or_lookalike_redirects(self):
        locations = ['http://test-team.cloudflareaccess.com/cdn-cgi/access/login/x',
                     'https://test-team.cloudflareaccess.com.evil.example/cdn-cgi/access/login/x',
                     'https://extra.test-team.cloudflareaccess.com/cdn-cgi/access/login/x',
                     'https://test-team.cloudflareaccess.com:8443/cdn-cgi/access/login/x',
                     'https://test-team.cloudflareaccess.com:invalid/cdn-cgi/access/login/x',
                     'https://user@test-team.cloudflareaccess.com/cdn-cgi/access/login/x',
                     'https://test-team.cloudflareaccess.com/cdn-cgi/access/login-evil/x',
                     'https://test-team.cloudflareaccess.com/other',
                     'https://127.0.0.1/cdn-cgi/access/login/x',
                     'https://test-team.cloud\nflareaccess.com/cdn-cgi/access/login/x']
        for location in locations:
            with self.subTest(location=location):
                response = MagicMock(status=302, headers={'Location': location})
                with self.assertRaises(RuntimeError):
                    installer.access_login_team(response)

    def test_unprotected_page_cannot_become_an_access_identity(self):
        opener = self.opener('', status=200)
        with patch.object(installer.urllib.request, 'build_opener', return_value=opener), \
             patch.object(installer, 'ask', return_value='test-team'):
            team = installer.resolve_access_team(CONFIG['domain'])
            with self.assertRaises(RuntimeError):
                installer.verify_edge(dict(CONFIG, team=team))

    def test_detected_team_still_requires_valid_public_key_response(self):
        opener = self.opener('https://test-team.cloudflareaccess.com/cdn-cgi/access/login/example')
        key_response = MagicMock()
        key_response.__enter__.return_value = key_response
        key_response.read.return_value = b'{"keys":[]}'
        redirect = MagicMock(status=302, headers={
            'Location': 'https://test-team.cloudflareaccess.com/cdn-cgi/access/login/example'})
        redirect.__enter__.return_value = redirect
        opener.open.side_effect = [redirect]*4 + [key_response]
        with patch.object(installer.urllib.request, 'build_opener', return_value=opener), \
             patch.object(installer, 'ask', side_effect=AssertionError('Unexpected manual team prompt')):
            team = installer.resolve_access_team(CONFIG['domain'])
            with self.assertRaisesRegex(RuntimeError, 'RS256'):
                installer.verify_edge(dict(CONFIG, team=team))

    def test_reject_unprotected_or_other_team(self):
        for location, status in (('', 200), ('', 502), ('http://test-team.cloudflareaccess.com/cdn-cgi/access/login/x', 302),
                                 ('https://evil.example/cdn-cgi/access/login/x', 302),
                                 ('https://test-team.cloudflareaccess.com/unrelated', 302)):
            with self.subTest(location=location, status=status):
                with patch.object(installer.urllib.request, 'build_opener', return_value=self.opener(location, status)):
                    with self.assertRaises(RuntimeError):
                        installer.verify_edge(CONFIG)

    def test_saved_team_cannot_switch_to_another_valid_team(self):
        opener = self.opener('https://another-team.cloudflareaccess.com/cdn-cgi/access/login/example')
        with patch.object(installer.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(RuntimeError, '团队不一致'):
                installer.verify_edge(CONFIG)
        self.assertEqual(opener.open.call_count, 1)


class PersistenceTests(unittest.TestCase):
    def test_secrets_generated_once_root_only_and_not_in_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(installer, 'ROOT', root):
                installer.create_secrets(os.getuid(), os.getgid())
                password = (root/'secrets/web-password').read_text()
                key = (root/'secrets/session-key').read_text()
                installer.create_secrets(os.getuid(), os.getgid())
            self.assertEqual((root/'secrets/web-password').read_text(), password)
            self.assertEqual((root/'secrets/session-key').read_text(), key)
            self.assertGreaterEqual(len(password.strip()), 32)
            self.assertEqual(len(key.strip()), 64)
            self.assertEqual((root/'secrets/web-password').stat().st_mode & 0o777, 0o400)
            self.assertEqual((root/'credentials.txt').stat().st_mode & 0o777, 0o600)

    def test_existing_unmarked_volume_never_formatted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data.ext4').write_bytes(b'pre-existing volume')
            with patch.object(installer, 'DATA', root), patch.object(installer, 'run') as run:
                with self.assertRaises(RuntimeError):
                    installer.create_volume(dict(CONFIG, storage_gib=16))
                run.assert_not_called()
            self.assertEqual((root/'data.ext4').read_bytes(), b'pre-existing volume')

    def test_failed_access_gate_never_builds_or_starts_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'runtime'
            state = root/'install-state.json'
            root.mkdir()
            state.write_text(json.dumps(dict(CONFIG, complete=False)))
            with patch.multiple(installer, ROOT=root, STATE=state), \
                 patch.object(installer, 'platform_check', return_value=({}, 8*1024**3)), \
                 patch.object(installer, 'run') as run, \
                 patch.object(installer, 'setup_tunnel'), patch.object(installer, 'ask'), \
                 patch.object(installer, 'resolve_access_team') as resolve, \
                 patch.object(installer, 'verify_edge', side_effect=RuntimeError('no access gate')), \
                 patch.object(installer, 'dependencies') as dependencies, \
                 patch.object(installer, 'install_services') as services:
                with self.assertRaises(RuntimeError):
                    installer.install()
                dependencies.assert_not_called()
                services.assert_not_called()
                resolve.assert_not_called()
                self.assertFalse(any(call.args[:2] == ('docker', 'build') for call in run.call_args_list))

    def test_fresh_install_resolves_team_after_tunnel_and_before_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'runtime'
            order = []
            def detect(domain):
                self.assertEqual(domain, CONFIG['domain'])
                self.assertEqual(order, ['tunnel'])
                order.append('discovery')
                return 'test-team'
            with patch.multiple(installer, ROOT=root, STATE=root/'install-state.json'), \
                 patch.object(installer, 'platform_check', return_value=({}, 8*1024**3)), \
                 patch.object(installer, 'preflight'), patch.object(installer, 'run'), \
                 patch.object(installer, 'ask', side_effect=['wechat.example.com', 'a'*64, 'owner@example.com', '16', 'DEPLOY', '']) as ask, \
                 patch.object(installer, 'setup_tunnel', side_effect=lambda: order.append('tunnel')), \
                 patch.object(installer, 'resolve_access_team', side_effect=detect), \
                 patch.object(installer, 'verify_edge', side_effect=RuntimeError('test gate stops deployment')) as verify, \
                 patch.object(installer, 'dependencies') as dependencies:
                with self.assertRaises(RuntimeError):
                    installer.install()
                self.assertEqual(order, ['tunnel', 'discovery'])
                self.assertEqual(ask.call_count, 6)
                self.assertEqual(verify.call_args.args[0]['team'], 'test-team')
                self.assertEqual(verify.call_args.args[0]['aud'], CONFIG['aud'])
                dependencies.assert_not_called()
                self.assertFalse(root.exists())

    def test_completed_install_never_regenerates_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'install-state.json'
            state.write_text(json.dumps(dict(CONFIG, complete=True)))
            with patch.object(installer, 'STATE', state), \
                 patch.object(installer, 'platform_check', return_value=({}, 8*1024**3)), \
                 patch.object(installer, 'doctor'), patch.object(installer, 'create_secrets') as create, \
                 patch.object(installer, 'run') as run:
                installer.install()
                create.assert_not_called()
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
