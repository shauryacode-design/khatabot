"""Encryption for optional customer phone numbers."""

from cryptography.fernet import Fernet


def encrypt_phone(phone: str, encryption_key: str) -> str:
    normalized = "".join(character for character in phone if character.isdigit() or character == "+")
    if not normalized:
        raise ValueError("Phone number must contain digits.")
    return Fernet(encryption_key.encode("ascii")).encrypt(
        normalized.encode("utf-8")
    ).decode("ascii")


def decrypt_phone(encrypted_phone: str, encryption_key: str) -> str:
    return Fernet(encryption_key.encode("ascii")).decrypt(
        encrypted_phone.encode("ascii")
    ).decode("utf-8")
