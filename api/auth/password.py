"""password.py — bcrypt password hashing (using bcrypt directly, passlib-free).

passlib 1.7.4 is incompatible with bcrypt ≥ 4.x (__about__ removed).
We call bcrypt directly to avoid the incompatibility.
"""
import bcrypt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False
