"""Verify Cloudflare Access at the origin; never log tokens or user identities."""
import json
import base64
import binascii
import functools
import hmac
import logging
import os
import re
import threading
import time
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
import bcrypt

TEAM = os.environ["WECHAT_ACCESS_TEAM"]
DOMAIN = os.environ["WECHAT_DOMAIN"]
if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", TEAM):
    raise ValueError("Invalid Cloudflare team name")
if not re.fullmatch(r"[a-z0-9.-]+", DOMAIN) or "." not in DOMAIN:
    raise ValueError("Invalid application hostname")
ISSUER = f"https://{TEAM}.cloudflareaccess.com"
AUDIENCE = os.environ["WECHAT_ACCESS_AUD"]
ORIGIN = "https://" + DOMAIN
ALLOWED_EMAILS = frozenset(x.strip().casefold() for x in os.environ["WECHAT_ALLOWED_EMAILS"].split(",") if x.strip())
if not ALLOWED_EMAILS:
    raise ValueError("An explicit email allowlist is required")
MAX_TOKEN_BYTES = 16384
SESSION_COOKIE = "__Host-wechat_session"
SESSION_ISSUER = "wechat-origin-session"
SESSION_AUDIENCE = DOMAIN
SESSION_DURATION = 30 * 24 * 60 * 60
KEYS = {}
KEYS_REFRESHED = 0.0
KEYS_ATTEMPTED = 0.0
KEY_LOCK = threading.Lock()


def signing_key(kid):
    global KEYS, KEYS_REFRESHED, KEYS_ATTEMPTED
    with KEY_LOCK:
        now = time.monotonic()
        if now - KEYS_REFRESHED >= 3600 or kid not in KEYS:
            if now - KEYS_ATTEMPTED >= 60:
                KEYS_ATTEMPTED = now
                request = urllib.request.Request(
                    ISSUER + "/cdn-cgi/access/certs",
                    headers={"User-Agent": "wechat-access-origin-verifier/1.0"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    data = response.read(262145)
                if len(data) > 262144:
                    raise ValueError("Oversized signing key response")
                parsed = json.loads(data)
                keys = {
                    item["kid"]: jwt.PyJWK.from_dict(item).key
                    for item in parsed["keys"]
                    if item.get("kty") == "RSA" and item.get("alg") == "RS256"
                }
                if not keys:
                    raise ValueError("Empty signing key response")
                KEYS, KEYS_REFRESHED = keys, now
        if now - KEYS_REFRESHED > 7200 or kid not in KEYS:
            raise ValueError("Unavailable signing key")
        return KEYS[kid]


def access_claims(token, origin=""):
    if origin and origin != ORIGIN:
        return None
    if not token or len(token) > MAX_TOKEN_BYTES:
        return None
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            return None
        claims = jwt.decode(
            token,
            signing_key(header["kid"]),
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            leeway=10,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub"]},
        )
        if claims.get("type") == "app" and claims.get("sub") and isinstance(claims.get("email"), str) and claims["email"].casefold() in ALLOWED_EMAILS:
            return claims
    except Exception:
        pass
    return None


def verify(token, origin=""):
    return access_claims(token, origin) is not None


@functools.lru_cache(maxsize=1)
def session_config():
    config = json.loads(Path("/run/wechat-session.json").read_text())
    if len(bytes.fromhex(config["key"])) != 32 or len(config["credential_version"]) != 64:
        raise ValueError("Invalid session configuration")
    return config


def valid_session(cookies, identity, config):
    try:
        parsed = SimpleCookie()
        parsed.load(cookies)
        value = parsed.get(SESSION_COOKIE)
        if value is None or len(value.value) > MAX_TOKEN_BYTES:
            return False
        claims = jwt.decode(
            value.value, bytes.fromhex(config["key"]), algorithms=["HS256"],
            issuer=SESSION_ISSUER, audience=SESSION_AUDIENCE,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "credential_version"]},
        )
        return (claims.get("version") == 1
                and hmac.compare_digest(claims["sub"], identity["sub"])
                and hmac.compare_digest(claims["credential_version"], config["credential_version"]))
    except Exception:
        return False


def valid_password(authorization, config):
    try:
        if len(authorization) > 2048:
            return False
        scheme, encoded = authorization.split(" ", 1)
        if scheme.lower() != "basic":
            return False
        username, password = base64.b64decode(encoded, validate=True).split(b":", 1)
        if not hmac.compare_digest(username, b"wechat") or len(password) > 72:
            return False
        return bcrypt.checkpw(password, config["password_hash"].encode("ascii"))
    except (ValueError, TypeError, binascii.Error):
        return False


def session_cookie(identity, config):
    now = int(time.time())
    value = jwt.encode(
        {"iss": SESSION_ISSUER, "aud": SESSION_AUDIENCE, "sub": identity["sub"],
         "iat": now, "nbf": now, "exp": now + SESSION_DURATION,
         "credential_version": config["credential_version"], "version": 1},
        bytes.fromhex(config["key"]), algorithm="HS256",
    )
    return (f"{SESSION_COOKIE}={value}; Path=/; Secure; HttpOnly; SameSite=Lax; "
            f"Max-Age={SESSION_DURATION}")


def authorize(token, origin="", cookies="", authorization=""):
    # A persistent local session never substitutes for current Access verification.
    identity = access_claims(token, origin)
    if identity is None:
        return 403, ""
    try:
        config = session_config()
        if valid_session(cookies, identity, config):
            return 204, ""
        if valid_password(authorization, config):
            return 204, session_cookie(identity, config)
        return 401, ""
    except Exception:
        return 403, ""


class Handler(BaseHTTPRequestHandler):
    server_version = "OriginVerifier"

    def do_GET(self):
        if self.path != "/verify":
            self.send_error(404)
            return
        status, cookie = authorize(
            self.headers.get("Cf-Access-Jwt-Assertion", ""),
            self.headers.get("Origin", ""),
            self.headers.get("Cookie", ""),
            self.headers.get("Authorization", ""),
        )
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="WeChat", charset="UTF-8"')
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, *_args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    slots = threading.BoundedSemaphore(16)

    def process_request(self, request, address):
        request.settimeout(5)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    session_config()
    Server(("127.0.0.1", 8090), Handler).serve_forever()
