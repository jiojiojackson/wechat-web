"""Destructive removal tests use private temporary trees and a fake host."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('remover', ROOT/'deploy/uninstall.py')
remover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remover)


class FakeHost:
    def __init__(self):
        self.calls = []
        self.ref = 'jiojiojackson/web-wechat@sha256:'+'a'*64
        self.app = {'Id': 'app-id', 'Image': 'image-id',
                    'Config': {'Labels': {'com.docker.compose.project': 'wechat-web'}},
                    'Mounts': [{'Source': str(remover.DATA/'config'), 'Destination': '/config'}]}
        self.containers = {'app-id': self.app}
        self.network = {'Id': 'net-id', 'Driver': 'bridge',
                        'Options': {'com.docker.network.bridge.name': 'br-wxweb'},
                        'Containers': {'app-id': {}}}
        self.images = [{'Id': 'image-id', 'RepoTags': ['jiojiojackson/web-wechat:latest', 'other-app:stable'],
                        'RepoDigests': [self.ref]}]
        self.chains = {c for _, _, c in remover.CHAINS}
        self.jumps = set(self.chains)

    def __call__(self, *args, allowed=(0,)):
        args = tuple(str(x) for x in args)
        self.calls.append(args)
        output, code = '', 0
        if args[0] == 'systemd-escape':
            output = 'app-data.mount'
        elif args[0] == 'systemctl':
            if args[1] == 'show':
                output = 'loaded' if (remover.UNITS/args[2]).exists() else 'not-found'
        elif args[:2] == ('docker', 'info'):
            output = '29.9.2'
        elif args[:3] == ('docker', 'container', 'ls'):
            output = '\n'.join(k for k in self.containers if '--filter' not in args or k == 'app-id')
        elif args[:3] == ('docker', 'container', 'inspect'):
            output = json.dumps([self.containers[k] for k in args[3:]])
        elif args[:3] == ('docker', 'container', 'rm'):
            self.containers.pop(args[-1])
            if self.network:
                self.network['Containers'].pop(args[-1], None)
        elif args[:3] == ('docker', 'network', 'ls'):
            output = 'net-id' if self.network else ''
        elif args[:3] == ('docker', 'network', 'inspect'):
            output = json.dumps([self.network])
        elif args[:3] == ('docker', 'network', 'rm'):
            assert not self.network['Containers']
            self.network = None
        elif args[:3] == ('docker', 'image', 'ls'):
            output = '\n'.join(x['Id'] for x in self.images)
        elif args[:3] == ('docker', 'image', 'inspect'):
            images = [x for x in self.images if any(k == x['Id'] or k in x['RepoTags']+x['RepoDigests'] for k in args[3:])]
            output, code = json.dumps(images), 0 if images else 1
        elif args[:3] == ('docker', 'image', 'rm'):
            for image in self.images:
                for key in ('RepoTags', 'RepoDigests'):
                    if args[-1] in image[key]:
                        image[key].remove(args[-1])
        elif args[0] in ('iptables', 'ip6tables'):
            operation = args[2]
            if operation == '-C':
                code = 0 if args[-1] in self.jumps else 1
            elif operation == '-D':
                self.jumps.remove(args[-1])
            elif operation == '-S':
                code = 0 if args[3] in self.chains else 1
            elif operation == '-X':
                self.chains.remove(args[3])
            elif operation != '-F':
                raise AssertionError(args)
        else:
            raise AssertionError(args)
        if code not in allowed:
            raise RuntimeError('Fake host command failed: '+str(args))
        return subprocess.CompletedProcess(args, code, stdout=output, stderr='')


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        values = {name: base/name.lower() for name in
                  ('ROOT', 'DATA', 'BACKUPS', 'UNITS', 'BIN', 'LOCKS', 'TIMERS')}
        values['APT_CONFIG'] = base/'apt/52wechat-web-security'
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.multiple(remover, **values))
        self.patches.enter_context(patch.object(remover.os, 'geteuid', return_value=0))
        self.patches.enter_context(patch.object(remover.shutil, 'which', side_effect=lambda x: '/bin/'+x))
        self.patches.enter_context(patch.object(remover, 'mounts', return_value=[]))
        self.patches.enter_context(contextlib.redirect_stdout(io.StringIO()))
        files = [remover.ROOT/'install-state.json', remover.ROOT/'secrets/session-key',
                 remover.DATA/'data.ext4', remover.BACKUPS/'chat-backup.tar.gz',
                 remover.APT_CONFIG, remover.TIMERS/'stamp-wechat-web-backup.timer']
        files += [remover.UNITS/x for x in (*remover.SERVICE_NAMES, 'app-data.mount')]
        files += [remover.UNITS/'multi-user.target.wants'/x for x in remover.SERVICE_NAMES]
        files += [remover.BIN/x for x in ('wechat-web', 'wechat-web-firewall', 'wechat-web-backup')]
        files += [remover.LOCKS/x for x in ('wechat-web-install.lock', 'wechat-web-firewall.lock', 'wechat-web-backup.lock')]
        self.shared = [remover.UNITS/'cloudflared.service', remover.UNITS/'cloudflared-wechat-web.service',
                       remover.UNITS/'wechat-firewall.service', remover.BIN/'wechat-firewall',
                       base/'etc/cloudflared-wechat-web/token', base/'etc/docker/daemon.json',
                       base/'etc/apt/sources.list.d/wechat-web-docker.list']
        for path in files+self.shared:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('disposable fixture\n')
        self.host = FakeHost()
        self.patches.enter_context(patch.object(remover, 'command', side_effect=self.host))

    def test_removes_owned_resources_preserving_tunnel_shared_services_and_image_alias(self):
        remover.uninstall(assume_yes=True)
        for path in (remover.ROOT, remover.DATA, remover.BACKUPS, remover.APT_CONFIG):
            self.assertFalse(path.exists(), str(path))
        for name in (*remover.SERVICE_NAMES, 'app-data.mount'):
            self.assertFalse((remover.UNITS/name).exists())
        self.assertFalse(any(p.name in (*remover.SERVICE_NAMES, 'app-data.mount') for p in remover.UNITS.rglob('*')))
        for name in ('wechat-web', 'wechat-web-firewall', 'wechat-web-backup'):
            self.assertFalse((remover.BIN/name).exists())
        self.assertFalse(list(remover.LOCKS.glob('wechat-web*')))
        self.assertFalse(self.host.containers)
        self.assertIsNone(self.host.network)
        self.assertFalse(self.host.chains)
        self.assertFalse(self.host.jumps)
        self.assertEqual(self.host.images[0]['RepoTags'], ['other-app:stable'])
        self.assertEqual(self.host.images[0]['RepoDigests'], [])
        for path in self.shared:
            self.assertEqual(path.read_text(), 'disposable fixture\n')
        self.assertFalse(any('prune' in call or any('cloudflared' in x for x in call) for call in self.host.calls))

    def test_cancellation_does_not_stop_or_delete_anything(self):
        with self.assertRaisesRegex(RuntimeError, '取消'):
            remover.uninstall(ask=lambda _: '')
        self.assertFalse(self.host.calls)
        self.assertTrue(remover.DATA.exists())

    def test_foreign_container_is_rejected_before_mutations(self):
        self.host.app['Config']['Labels'] = {'com.docker.compose.project': 'other'}
        with self.assertRaisesRegex(RuntimeError, '容器不属于'):
            remover.uninstall(assume_yes=True)
        self.assertTrue(remover.ROOT.exists())
        self.assertFalse(any(call[:2] == ('systemctl', 'stop') for call in self.host.calls))

    def test_foreign_network_is_rejected_before_mutations(self):
        self.host.network['Options'] = {'com.docker.network.bridge.name': 'other-bridge'}
        with self.assertRaisesRegex(RuntimeError, '网络不属于'):
            remover.uninstall(assume_yes=True)
        self.assertTrue(remover.DATA.exists())

    def test_other_container_on_app_network_is_not_disconnected(self):
        self.host.network['Containers']['other-id'] = {}
        with self.assertRaisesRegex(RuntimeError, '其他容器'):
            remover.uninstall(assume_yes=True)
        self.assertFalse(any(call[:2] == ('systemctl', 'stop') for call in self.host.calls))

    def test_image_used_by_another_container_is_retained(self):
        other = {'Id': 'other-id', 'Image': 'image-id'}
        self.host.containers['other-id'] = other
        remover.uninstall(assume_yes=True)
        self.assertEqual(self.host.containers, {'other-id': other})
        self.assertIn(self.host.ref, self.host.images[0]['RepoDigests'])
        self.assertFalse(any(call[:3] == ('docker', 'image', 'rm') for call in self.host.calls))

    def test_extra_mount_blocks_recursive_deletion(self):
        with patch.object(remover, 'mounts', return_value=[(remover.DATA/'config/foreign', '8:1', 'ext4')]):
            with self.assertRaisesRegex(RuntimeError, '额外挂载'):
                remover.uninstall(assume_yes=True)
        self.assertFalse(self.host.calls)
        self.assertTrue(remover.DATA.exists())

    def test_symlinked_data_path_blocks_deletion(self):
        import shutil
        shutil.rmtree(remover.DATA)
        target = Path(self.temporary.name)/'foreign-data'
        target.mkdir()
        (target/'keep').write_text('keep')
        remover.DATA.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, '符号链接'):
            remover.uninstall(assume_yes=True)
        self.assertEqual((target/'keep').read_text(), 'keep')
        self.assertFalse(self.host.calls)

    def test_removal_can_be_repeated_from_source(self):
        remover.uninstall(assume_yes=True)
        remover.uninstall(assume_yes=True)
        self.assertFalse(remover.ROOT.exists())
        for path in self.shared:
            self.assertTrue(path.exists())

    def test_non_root_entry_is_rejected(self):
        with patch.object(remover.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(RuntimeError, 'sudo'):
                remover.uninstall(assume_yes=True)
        self.assertFalse(self.host.calls)

    def test_reference_matching_never_selects_another_repository(self):
        self.assertTrue(remover.project_reference(self.host.ref))
        for value in ('jiojiojackson/web-wechat-evil:latest', 'evil.example/jiojiojackson/web-wechat:latest', 'other:latest'):
            self.assertFalse(remover.project_reference(value))


if __name__ == '__main__':
    unittest.main()
