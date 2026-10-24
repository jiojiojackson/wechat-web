"""Exercise Compose's overlap with a stopped container's stale health result."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WaitReadyTests(unittest.TestCase):
    def run_waiter(self, states):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state.json'
            state.write_text(json.dumps({'states': states, 'calls': 0}))
            docker = root / 'docker'
            docker.write_text(f'#!{sys.executable}\n' + '''import json,os,sys
from pathlib import Path
p=Path(os.environ['WECHAT_READY_FIXTURE']);d=json.loads(p.read_text())
running,health=d['states'][min(d['calls'],len(d['states'])-1)]
d['calls']+=1;p.write_text(json.dumps(d))
print((str(running).lower()+' ' if '.State.Running' in ' '.join(sys.argv) else '')+health)
''')
            docker.chmod(0o755)
            sleeper = root / 'sleep'
            sleeper.write_text('#!/bin/sh\nexit 0\n')
            sleeper.chmod(0o755)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       WECHAT_READY_FIXTURE=str(state))
            result = subprocess.run(['bash', str(ROOT/'deploy/wait-ready.sh')],
                                    env=env, capture_output=True, text=True, timeout=20)
            return result, json.loads(state.read_text())['calls']

    def test_stopped_unhealthy_does_not_abort_recreation(self):
        result, calls = self.run_waiter([(False, 'unhealthy'), (True, 'starting'), (True, 'healthy')])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 3)

    def test_stopped_healthy_is_not_ready(self):
        result, calls = self.run_waiter([(False, 'healthy'), (True, 'starting'), (True, 'healthy')])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 3)

    def test_running_unhealthy_fails(self):
        result, calls = self.run_waiter([(True, 'unhealthy')])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 1)
        self.assertIn('健康检查失败', result.stderr)

    def test_never_started_times_out(self):
        result, calls = self.run_waiter([(False, 'healthy')])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 100)
        self.assertIn('就绪超时', result.stderr)


if __name__ == '__main__':
    unittest.main()
