"""Security regression checks using disposable signing keys, never live credentials."""
import importlib.util
import os
import time
import unittest
import base64
from unittest.mock import patch

import jwt
import bcrypt
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ.setdefault("WECHAT_ACCESS_AUD", "test-audience")
os.environ.setdefault("WECHAT_ACCESS_TEAM", "test-team")
os.environ.setdefault("WECHAT_DOMAIN", "wechat.example.com")
os.environ.setdefault("WECHAT_ALLOWED_EMAILS", "test@example.invalid")
from pathlib import Path
guard_path = (Path(os.environ["WECHAT_GUARD_PATH"]) if "WECHAT_GUARD_PATH" in os.environ
              else Path(__file__).resolve().parents[1]/"deploy/access_guard.py")
spec = importlib.util.spec_from_file_location("guard", guard_path)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class AccessGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def claims(self, **updates):
        now = int(time.time())
        result = dict(iss=guard.ISSUER, aud=guard.AUDIENCE, sub="test-user",
                      email="test@example.invalid", type="app", iat=now, nbf=now, exp=now + 300)
        result.update(updates)
        return result

    def check_claims(self, claims, expected, origin=""):
        token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})
        with patch.object(guard, "signing_key", return_value=self.key.public_key()):
            self.assertEqual(guard.verify(token, origin), expected)

    def test_valid_identity(self):
        self.check_claims(self.claims(), True, guard.ORIGIN)

    def test_email_allowlist_cannot_be_bypassed_by_broad_cloud_policy(self):
        self.check_claims(self.claims(email='stranger@example.invalid'), False)

    def test_email_case_insensitive(self):
        self.check_claims(self.claims(email='TEST@EXAMPLE.INVALID'), True)

    def test_wrong_audience(self):
        self.check_claims(self.claims(aud="different-app"), False)

    def test_wrong_issuer(self):
        self.check_claims(self.claims(iss="https://attacker.invalid"), False)

    def test_expired(self):
        self.check_claims(self.claims(exp=int(time.time()) - 60), False)

    def test_not_yet_valid(self):
        self.check_claims(self.claims(nbf=int(time.time()) + 60), False)

    def test_wrong_origin(self):
        self.check_claims(self.claims(), False, "https://attacker.invalid")

    def test_org_token(self):
        self.check_claims(self.claims(type="org"), False)

    def test_service_token(self):
        self.check_claims(self.claims(email="", sub=""), False)

    def test_required_expiry(self):
        claims = self.claims()
        claims.pop("exp")
        self.check_claims(claims, False)

    def test_missing_or_malformed(self):
        for token in ("", "invalid", "a.b.c", "a" * 16385):
            self.assertFalse(guard.verify(token))

    def test_wrong_signature(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(self.claims(), other, algorithm="RS256", headers={"kid": "test-key"})
        with patch.object(guard, "signing_key", return_value=self.key.public_key()):
            self.assertFalse(guard.verify(token))

    def test_algorithm_confusion(self):
        token = jwt.encode(self.claims(), "test-secret-with-at-least-32-bytes-long", algorithm="HS256", headers={"kid": "test-key"})
        self.assertFalse(guard.verify(token))


class PersistentSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = b'test-password'
        cls.config = {'key': 'ab'*32, 'credential_version': 'cd'*32,
                      'password_hash': bcrypt.hashpw(cls.password, bcrypt.gensalt(rounds=4)).decode()}
        cls.identity = {'sub':'verified-user','email':'test@example.invalid'}

    def setUp(self):
        self.key_patch = patch.object(guard,'session_config',return_value=dict(self.config))
        self.access_patch = patch.object(guard,'access_claims',return_value=dict(self.identity))
        self.key_patch.start();self.access_patch.start()
        self.addCleanup(self.key_patch.stop);self.addCleanup(self.access_patch.stop)

    def login_cookie(self):
        auth='Basic '+base64.b64encode(b'wechat:'+self.password).decode()
        status,cookie=guard.authorize('access-token',authorization=auth)
        self.assertEqual(status,204)
        return cookie

    def test_password_creates_protected_cookie(self):
        cookie=self.login_cookie()
        for attribute in ('__Host-wechat_session=','; Secure','; HttpOnly','; SameSite=Lax','; Path=/','; Max-Age=2592000'):
            self.assertIn(attribute,cookie)
        self.assertNotIn('Domain=',cookie)

    def test_refresh_and_websocket_need_no_password(self):
        cookie=self.login_cookie().split(';',1)[0]
        with patch.object(guard,'valid_password',side_effect=AssertionError('Password was requested again')):
            for _ in range(5):
                self.assertEqual(guard.authorize('access-token',guard.ORIGIN,cookie),(204,''))

    def test_session_does_not_replace_access(self):
        cookie=self.login_cookie()
        with patch.object(guard,'access_claims',return_value=None):
            self.assertEqual(guard.authorize('',cookies=cookie),(403,''))

    def test_session_bound_to_access_identity(self):
        cookie=self.login_cookie()
        with patch.object(guard,'access_claims',return_value={'sub':'different-user'}):
            self.assertEqual(guard.authorize('access-token',cookies=cookie)[0],401)

    def test_forged_cookie_rejected(self):
        self.assertEqual(guard.authorize('access-token',cookies='__Host-wechat_session=forged')[0],401)

    def test_expired_cookie_rejected(self):
        with patch.object(guard.time,'time',return_value=time.time()-guard.SESSION_DURATION-60):
            cookie=guard.session_cookie(self.identity,self.config)
        self.assertEqual(guard.authorize('access-token',cookies=cookie)[0],401)

    def test_password_rotation_invalidates_sessions(self):
        cookie=self.login_cookie()
        with patch.object(guard,'session_config',return_value=dict(self.config,credential_version='ef'*32)):
            self.assertEqual(guard.authorize('access-token',cookies=cookie)[0],401)

    def test_key_rotation_invalidates_sessions(self):
        cookie=self.login_cookie()
        with patch.object(guard,'session_config',return_value=dict(self.config,key='12'*32)):
            self.assertEqual(guard.authorize('access-token',cookies=cookie)[0],401)

    def test_new_bcrypt_salt_preserves_existing_sessions(self):
        cookie=self.login_cookie()
        config=dict(self.config,password_hash=bcrypt.hashpw(self.password,bcrypt.gensalt(rounds=4)).decode())
        with patch.object(guard,'session_config',return_value=config):
            self.assertEqual(guard.authorize('access-token',cookies=cookie),(204,''))

    def test_wrong_password_rejected(self):
        auth='Basic '+base64.b64encode(b'wechat:wrong-password').decode()
        self.assertEqual(guard.authorize('access-token',authorization=auth),(401,''))


if __name__ == "__main__":
    unittest.main()
