"""Google's supported signature verifier, bounded transport, and claim checks."""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import hmac
import json
import math
import re
from threading import Lock
import unicodedata

from cachecontrol import CacheControl
from email_validator import EmailNotValidError, validate_email
from google.auth import crypt
from google.auth import exceptions as google_errors
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
import requests


class GoogleTokenInvalid(Exception):
    def __init__(self):
        super().__init__("Google credential is invalid.")


class GoogleVerificationUnavailable(Exception):
    def __init__(self):
        super().__init__("Google verification is unavailable.")


@dataclass(frozen=True, repr=False)
class GoogleIdentity:
    subject: str = field(repr=False)
    email: str = field(repr=False)
    display_name: str


class _PlainName(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def display_name(value) -> str:
    if not isinstance(value, str):
        return "User"
    parser = _PlainName()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        return "User"
    text = "".join(parser.parts)
    text = "".join(
        " " if character.isspace() else character
        for character in text
        if character.isspace() or unicodedata.category(character) not in {"Cc", "Cf", "Cs"}
    )
    return " ".join(text.split())[:200] or "User"


def validate_claims(claims, client_id: str, nonce_hash: bytes, now: datetime) -> GoogleIdentity:
    if not isinstance(claims, dict):
        raise GoogleTokenInvalid()
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise GoogleTokenInvalid()
    if claims.get("aud") != client_id or ("azp" in claims and claims["azp"] != client_id):
        raise GoogleTokenInvalid()
    issued, expires = claims.get("iat"), claims.get("exp")
    if any(
        type(value) not in {int, float} or not 0 <= value <= 253402300799 or not math.isfinite(value)
        for value in (issued, expires)
    ):
        raise GoogleTokenInvalid()
    if issued < 0 or issued > now.timestamp() or expires <= now.timestamp() or expires <= issued:
        raise GoogleTokenInvalid()
    if "nbf" in claims:
        not_before = claims["nbf"]
        if type(not_before) not in {int, float} or not 0 <= not_before <= now.timestamp() or not math.isfinite(not_before):
            raise GoogleTokenInvalid()
    subject, nonce, email = claims.get("sub"), claims.get("nonce"), claims.get("email")
    if not isinstance(subject, str) or not re.fullmatch(r"[\x21-\x7e]{1,255}", subject):
        raise GoogleTokenInvalid()
    if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce):
        raise GoogleTokenInvalid()
    if not hmac.compare_digest(hashlib.sha256(nonce.encode("ascii")).digest(), nonce_hash):
        raise GoogleTokenInvalid()
    if claims.get("email_verified") is not True or not isinstance(email, str) or not 1 <= len(email) <= 254:
        raise GoogleTokenInvalid()
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        raise GoogleTokenInvalid() from None
    if not 1 <= len(email) <= 254:
        raise GoogleTokenInvalid()
    return GoogleIdentity(subject, email, display_name(claims.get("name")))


class BoundedGoogleRequest(GoogleRequest):
    def __init__(self, session, timeout):
        super().__init__(session=session)
        self.timeout = timeout

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        try:
            response = super().__call__(
                url, method=method, body=body, headers=headers,
                timeout=self.timeout, allow_redirects=False,
            )
            if response.status != 200 or len(response.data) > 1024 * 1024:
                raise GoogleVerificationUnavailable()
            certificates = json.loads(response.data)
            # The OAuth2 cert endpoint uses an X.509 mapping. Reject a malformed
            # response instead of misclassifying a provider failure as bad login.
            if not isinstance(certificates, dict) or not certificates or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in certificates.items()
            ):
                raise GoogleVerificationUnavailable()
            for certificate in certificates.values():
                crypt.RSAVerifier.from_string(certificate)
            return response
        except (requests.RequestException, google_errors.GoogleAuthError, ValueError, UnicodeError):
            raise GoogleVerificationUnavailable() from None


class GoogleTokenVerifier:
    def __init__(self, settings):
        self.client_id = settings.google_client_id
        self.timeout = settings.google_timeout_seconds
        self._session = None
        self._lock = Lock()

    def verify(self, token: str) -> dict:
        if not self.client_id or not self._lock.acquire(timeout=self.timeout):
            raise GoogleVerificationUnavailable()
        try:
            if self._session is None:
                self._session = CacheControl(requests.Session())
            transport = BoundedGoogleRequest(self._session, self.timeout)
            try:
                return google_id_token.verify_oauth2_token(
                    token, transport, audience=self.client_id, clock_skew_in_seconds=0,
                )
            except (google_errors.TransportError, requests.RequestException):
                raise GoogleVerificationUnavailable() from None
            except (google_errors.GoogleAuthError, ValueError, TypeError, KeyError):
                raise GoogleTokenInvalid() from None
        finally:
            self._lock.release()

    def close(self):
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None
