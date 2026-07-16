import pytest
from unittest.mock import patch
from cryptography.fernet import InvalidToken
from passlib.context import CryptContext

from app.core.security import (
    _get_fernet,
    get_password_hash,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    encrypt_api_secret,
    decrypt_api_secret,
)
from jose import jwt
from app.core.config import get_settings

settings = get_settings()

_test_ctx = CryptContext(schemes=["sha256_crypt"], deprecated="auto")


@patch("app.core.security.pwd_context", _test_ctx)
def test_password_hashing():
    password = "supersecretpassword"
    hashed = get_password_hash(password)
    
    assert password != hashed
    assert verify_password(password, hashed) is True
    assert verify_password("wrongpassword", hashed) is False

def test_create_access_token():
    subject = 123
    token = create_access_token(subject=subject)
    
    assert isinstance(token, str)
    
    # Verify token
    payload = decode_access_token(token)
    assert payload.get("sub") == str(subject)
    assert payload.get("type") == "access"
    assert "exp" in payload

def test_create_refresh_token():
    subject = 123
    token = create_refresh_token(subject=subject)
    
    assert isinstance(token, str)
    
    # Verify token
    payload = decode_access_token(token)
    assert payload.get("sub") == str(subject)
    assert payload.get("type") == "refresh"
    assert "exp" in payload

def test_encrypt_decrypt_api_secret():
    plain_secret = "my_super_secret_api_key_123"
    
    # Encrypt
    encrypted = encrypt_api_secret(plain_secret)
    assert encrypted != plain_secret
    assert isinstance(encrypted, str)
    
    # Decrypt
    decrypted = decrypt_api_secret(encrypted)
    assert decrypted == plain_secret

def test_decrypt_invalid_secret_raises_error():
    invalid_encrypted = "this_is_not_a_valid_fernet_token"
    with pytest.raises(InvalidToken):
        decrypt_api_secret(invalid_encrypted)


def test_get_fernet_cached():
    """_get_fernet reuses the same Fernet instance across calls — the
    SHA256 KDF + Fernet() construction are otherwise repeated on every
    signed merchant API request."""
    _get_fernet.cache_clear()
    first = _get_fernet()
    second = _get_fernet()
    assert first is second
