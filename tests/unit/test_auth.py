"""
tests/unit/test_auth.py — Unit tests for JWT handler and password utilities.
No database, no FastAPI client — pure unit tests.
"""
import time
import pytest
from jose import jwt

from api.auth.jwt_handler import (
    _ALGO, _SECRET,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from api.auth.password import hash_password, verify_password
from data.models.user_model import UserRole


# ── Password ─────────────────────────────────────────────────────────────────

class TestPassword:
    def test_hash_is_not_plaintext(self):
        h = hash_password("mysecret")
        assert h != "mysecret"

    def test_verify_correct(self):
        h = hash_password("correct")
        assert verify_password("correct", h) is True

    def test_verify_wrong(self):
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_verify_empty_against_hash(self):
        h = hash_password("notempty")
        assert verify_password("", h) is False

    def test_different_hashes_same_password(self):
        # bcrypt generates a unique salt each time
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        assert verify_password("same", h1)
        assert verify_password("same", h2)

    def test_verify_bad_hash_returns_false(self):
        # Should not raise, just return False
        assert verify_password("x", "not-a-bcrypt-hash") is False


# ── JWT access token ──────────────────────────────────────────────────────────

class TestAccessToken:
    def test_encode_decode_roundtrip(self):
        tok = create_access_token("user-123", "a@b.com", UserRole.ADMIN)
        payload = decode_token(tok, "access")
        assert payload["sub"]   == "user-123"
        assert payload["email"] == "a@b.com"
        assert payload["role"]  == "admin"
        assert payload["type"]  == "access"

    def test_all_roles_encode(self):
        for role in UserRole:
            tok = create_access_token("uid", "x@y.com", role)
            p = decode_token(tok, "access")
            assert p["role"] == role.value

    def test_wrong_type_raises(self):
        from fastapi import HTTPException
        tok = create_access_token("uid", "x@y.com", UserRole.ANALYST)
        with pytest.raises(HTTPException) as exc_info:
            decode_token(tok, "refresh")  # access token used where refresh expected
        assert exc_info.value.status_code == 401

    def test_tampered_token_raises(self):
        from fastapi import HTTPException
        tok = create_access_token("uid", "x@y.com", UserRole.ANALYST)
        tampered = tok[:-4] + "xxxx"
        with pytest.raises(HTTPException) as exc_info:
            decode_token(tampered, "access")
        assert exc_info.value.status_code == 401

    def test_expired_token_raises(self):
        from fastapi import HTTPException
        from datetime import datetime, timedelta, timezone
        payload = {
            "sub":   "uid",
            "email": "x@y.com",
            "role":  "analyst",
            "type":  "access",
            "iat":   datetime.now(timezone.utc),
            "exp":   datetime.now(timezone.utc) - timedelta(seconds=1),  # already expired
        }
        tok = jwt.encode(payload, _SECRET, algorithm=_ALGO)
        with pytest.raises(HTTPException) as exc_info:
            decode_token(tok, "access")
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()


# ── JWT refresh token ─────────────────────────────────────────────────────────

class TestRefreshToken:
    def test_encode_decode_roundtrip(self):
        tok = create_refresh_token("user-456")
        payload = decode_token(tok, "refresh")
        assert payload["sub"]  == "user-456"
        assert payload["type"] == "refresh"

    def test_refresh_rejected_as_access(self):
        from fastapi import HTTPException
        tok = create_refresh_token("user-456")
        with pytest.raises(HTTPException) as exc_info:
            decode_token(tok, "access")
        assert exc_info.value.status_code == 401


# ── Auth schemas validation ───────────────────────────────────────────────────

class TestAuthSchemas:
    def test_user_create_valid(self):
        from api.schemas.auth_schema import UserCreate
        u = UserCreate(email="user@example.com", username="johndoe", password="strongpass")
        assert u.role == UserRole.ANALYST  # default

    def test_user_create_short_username_rejected(self):
        from pydantic import ValidationError
        from api.schemas.auth_schema import UserCreate
        with pytest.raises(ValidationError):
            UserCreate(email="a@b.com", username="ab", password="strongpass")

    def test_user_create_short_password_rejected(self):
        from pydantic import ValidationError
        from api.schemas.auth_schema import UserCreate
        with pytest.raises(ValidationError):
            UserCreate(email="a@b.com", username="validname", password="short")

    def test_user_create_invalid_email_rejected(self):
        from pydantic import ValidationError
        from api.schemas.auth_schema import UserCreate
        with pytest.raises(ValidationError):
            UserCreate(email="not-an-email", username="validname", password="strongpass")

    def test_login_request(self):
        from api.schemas.auth_schema import LoginRequest
        req = LoginRequest(email="user@example.com", password="pass1234")
        assert req.email == "user@example.com"

    def test_change_password_short_new_password_rejected(self):
        from pydantic import ValidationError
        from api.schemas.auth_schema import ChangePasswordRequest
        with pytest.raises(ValidationError):
            ChangePasswordRequest(current_password="oldpass123", new_password="short")
