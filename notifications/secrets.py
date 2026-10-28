"""Encrypt gateway credentials saved in the database with a key derived from DJANGO_SECRET_KEY."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key = hashlib.sha256(f'sms-settings:{settings.SECRET_KEY}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode() if value else ''


def decrypt(token):
    """Plain text, or None when it cannot be read (e.g. DJANGO_SECRET_KEY changed)."""
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
