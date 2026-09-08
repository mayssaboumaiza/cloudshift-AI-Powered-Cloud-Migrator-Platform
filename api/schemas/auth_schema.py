"""auth_schema.py — Pydantic models for auth and user endpoints."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator

from data.models.user_model import UserRole


# ── Register / Create ───────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email:    EmailStr
    username: str
    password: str
    role:     UserRole = UserRole.ANALYST

    @field_validator("username")
    @classmethod
    def username_alphanum(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 80:
            raise ValueError("Username too long (max 80)")
        return v

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


# ── Login ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int          # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


# ── User responses ──────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id:         str
    email:      str
    username:   str
    role:       UserRole
    is_active:  bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    username:  Optional[str]   = None
    role:      Optional[UserRole] = None
    is_active: Optional[bool]  = None
    password:  Optional[str]   = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


# ── Change password ─────────────────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str

    @field_validator("new_password")
    @classmethod
    def pw_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("New password must be at least 8 characters")
        return v


# ── Invitations ──────────────────────────────────────────────────────────────

class InviteRequest(BaseModel):
    email: EmailStr
    role:  UserRole = UserRole.ANALYST


class InviteResponse(BaseModel):
    id:         str
    email:      str
    role:       UserRole
    expires_at: datetime
    used:       bool

    model_config = {"from_attributes": True}


class InviteInfoResponse(BaseModel):
    """Public info returned when validating an invitation token."""
    email:      str
    role:       UserRole
    expires_at: datetime


class AcceptInviteRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_ok(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 80:
            raise ValueError("Username too long (max 80)")
        return v

    @field_validator("password")
    @classmethod
    def password_ok(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v
