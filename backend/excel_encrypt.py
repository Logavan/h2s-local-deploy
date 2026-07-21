import io
import os
import zipfile
import base64
import numpy as np
import pandas as pd
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger("sql_converter")


# --------- Fernet Key ---------
def get_fernet(password: str) -> Fernet:
    """Derive a Fernet instance from a password (you may already have your own)."""
    # For demo: just using password directly is not secure; replace with a KDF in real use.
    key = password.encode("utf-8").ljust(32, b"0")  # pad/truncate to 32 bytes
    return Fernet(base64.urlsafe_b64encode(key))


# --------- Encryption ---------
def encrypt_xlsx_buffer(xlsx_buffer: io.BytesIO | bytes, password: str) -> bytes:
    """Encrypt an Excel buffer (bytes or BytesIO) and return encrypted bytes."""
    f = get_fernet(password)

    if isinstance(xlsx_buffer, bytes):
        data = xlsx_buffer
    else:
        xlsx_buffer.seek(0)
        data = xlsx_buffer.read()

    return f.encrypt(data)


# --------- Decryption ---------
def decrypt_xlsx_file(file_obj: io.BytesIO | bytes, password: str) -> pd.ExcelFile:
    """Decrypt an encrypted Excel file (bytes or BytesIO) and return a pandas ExcelFile."""
    f = get_fernet(password)

    if isinstance(file_obj, bytes):
        encrypted_data = file_obj
    else:
        file_obj.seek(0)
        encrypted_data = file_obj.read()

    decrypted_data = f.decrypt(encrypted_data)
    return pd.ExcelFile(io.BytesIO(decrypted_data))

#----Decryption completed----------------------