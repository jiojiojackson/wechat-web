"""Real controlling-terminal tests; all system/network operations are fixtures."""
import os
from pathlib import Path
import pty
import select
import signal
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def terminal_session(code, exchanges):
    prefix = ('import importlib.util,os,tempfile;from pathlib import Path;'
              f's=importlib.util.spec_from_file_location("installer",{str(ROOT/"deploy/setup.py")!r});'
              'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
              'os.dup2(os.open(os.devnull,os.O_RDONLY),0);')
    pid, descriptor = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, '-u', '-c', prefix + code])
    transcript = b''
    position = 0
    status = None
    deadline = time.monotonic() + 15
    try:
        for expected, answer in exchanges:
            marker = expected.encode()
            while marker not in transcript[position:]:
                if time.monotonic() > deadline:
                    raise AssertionError(f'Terminal did not reach prompt: {expected}\n{transcript.decode(errors="replace")}')
                if select.select([descriptor], [], [], .1)[0]:
                    try:
                        part = os.read(descriptor, 65536)
                    except OSError:
                        part = b''
                    if not part:
                        raise AssertionError('Installer exited before prompt: '+expected+'\n'+transcript.decode(errors='replace'))
                    transcript += part
            position = len(transcript)
            os.write(descriptor, answer.encode())
        while time.monotonic() < deadline:
            if select.select([descriptor], [], [], .1)[0]:
                try:
                    part = os.read(descriptor, 65536)
                except OSError:
                    part = b''
                if part:
                    transcript += part
            found, status = os.waitpid(pid, os.WNOHANG)
            if found:
                return os.waitstatus_to_exitcode(status), transcript.decode(errors='replace')
        raise AssertionError('Terminal test timed out')
    finally:
        os.close(descriptor)
        if status is None or not found:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except ProcessLookupError:
                pass


class TerminalTests(unittest.TestCase):
    def test_tty_input_with_redirected_stdin_and_defaults(self):
        code = 'print("RESULT",m.ask("Name"),m.ask("Size","16"))'
        status, output = terminal_session(code, [('Name:', 'fixture\n'), ('Size [16]', '\n')])
        self.assertEqual(status, 0, output)
        self.assertIn('RESULT fixture 16', output)
        self.assertNotIn('not seekable', output)

    def test_secret_is_never_echoed(self):
        status, output = terminal_session('value=m.ask_secret("Secret");print("SECRET_OK",value=="fixture-hidden-value")',
                                          [('Secret:', 'fixture-hidden-value\n')])
        self.assertEqual(status, 0, output)
        self.assertIn('SECRET_OK True', output)
        self.assertNotIn('fixture-hidden-value', output)

    def test_full_installer_in_a_real_terminal_uses_compose_and_pull_only(self):
        code = '''
tmp=tempfile.TemporaryDirectory();m.ROOT=Path(tmp.name)/'runtime';m.STATE=m.ROOT/'install-state.json'
m.platform_check=lambda:({'VERSION_ID':'24.04'},8*1024**3)
m.preflight=lambda:None
m.setup_tunnel=lambda:print('TUNNEL_READY')
m.resolve_access_team=lambda domain:'test-team'
m.verify_edge=lambda config:None
m.dependencies=lambda info:print('DEPENDENCIES_READY')
m.pull_image=lambda:'jiojiojackson/web-wechat@sha256:'+'a'*64
m.create_volume=lambda config:(os.getuid(),os.getgid())
m.install_services=lambda:print('COMPOSE_STARTED')
m.doctor=lambda config:print('HEALTH_OK')
m.run=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('Unexpected host operation'))
m.install()
assert 'WECHAT_IMAGE=jiojiojackson/web-wechat@sha256:' in (m.ROOT/'.env').read_text()
assert (m.ROOT/'credentials.txt').stat().st_mode & 0o777 == 0o600
print('INSTALL_FLOW_OK')
tmp.cleanup()
'''
        status, output = terminal_session(code, [
            ('Cloudflare 的域名、Tunnel 和 Access 是否已配置', 'n\n'),
            ('微信完整域名', 'wechat.example.com\n'),
            ('Access 允许登录的本人邮箱', 'owner@example.com\n'),
            ('该微信路由已保存', '\n'),
            ('粘贴微信应用的 AUD Tag', 'a'*64+'\n'),
            ('微信数据卷大小', '\n'),
            ('确认部署？', 'y\n')])
        self.assertEqual(status, 0, output)
        self.assertIn('COMPOSE_STARTED', output)
        self.assertIn('INSTALL_FLOW_OK', output)
        self.assertIn('[6/6]', output)
        self.assertNotIn('not seekable', output)
        self.assertNotIn('微信 Web：域名 / Tunnel / Access 准备向导', output)


if __name__ == '__main__':
    unittest.main()
