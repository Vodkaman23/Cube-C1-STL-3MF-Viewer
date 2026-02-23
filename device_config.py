"""
CubeVi C1 device configuration loader
Reads per-device calibration values from CubeStage's encrypted deviceConfig.json
"""

import os
import json
import base64
import hashlib

# Default calibration (fallback if device config not found)
DEFAULT_SLOPE = 0.1021
DEFAULT_INTERVAL = 19.6169
DEFAULT_X0 = 3.59

# CubeStage config location
CONFIG_PATHS = [
    os.path.join(os.environ.get('APPDATA', ''), 'Cubestage', 'deviceConfig.json'),
    os.path.join(os.environ.get('APPDATA', ''), 'OpenstageAI', 'deviceConfig.json'),
]

# AES decryption passphrase (from CubeVi-Swizzle-Unity source)
_PASSPHRASE = b'3f5e1a2b4c6d7e8f9a0b1c2d3e4f5a6b'


def _evp_bytes_to_key(password, salt, key_len=32, iv_len=16):
    """OpenSSL EVP_BytesToKey with MD5, 1 iteration"""
    dtot = b''
    d = b''
    while len(dtot) < key_len + iv_len:
        d = hashlib.md5(d + password + salt).digest()
        dtot += d
    return dtot[:key_len], dtot[key_len:key_len + iv_len]


def _decrypt_config(encrypted_str):
    """Decrypt AES-256-CBC encrypted config string (OpenSSL format)"""
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
    except ImportError:
        print("  Warning: pycryptodome not installed, cannot decrypt device config")
        print("  Install with: pip install pycryptodome")
        return None

    data = base64.b64decode(encrypted_str)
    if data[:8] != b'Salted__':
        return None

    salt = data[8:16]
    ciphertext = data[16:]
    key, iv = _evp_bytes_to_key(_PASSPHRASE, salt)

    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return json.loads(plaintext.decode('utf-8'))


def load_device_config(debug=True):
    """
    Load CubeVi C1 calibration from CubeStage's deviceConfig.json

    Returns:
        dict with keys: slope, interval, x0, device_id
        Falls back to defaults if config not found
    """
    result = {
        'slope': DEFAULT_SLOPE,
        'interval': DEFAULT_INTERVAL,
        'x0': DEFAULT_X0,
        'device_id': None,
        'source': 'defaults'
    }

    for config_path in CONFIG_PATHS:
        if os.path.exists(config_path):
            if debug:
                print(f"  Found device config: {config_path}")
            try:
                with open(config_path, 'r') as f:
                    raw = json.load(f)

                encrypted = raw.get('config', '')
                decrypted = _decrypt_config(encrypted)

                if decrypted and 'config' in decrypted:
                    cfg = decrypted['config']
                    result['slope'] = cfg.get('obliquity', DEFAULT_SLOPE)
                    result['interval'] = cfg.get('lineNumber', DEFAULT_INTERVAL)
                    result['x0'] = cfg.get('deviation', DEFAULT_X0)
                    result['device_id'] = cfg.get('deviceId', None)
                    result['source'] = config_path

                    if debug:
                        print(f"  Device ID: {result['device_id']}")
                        print(f"  Calibration: slope={result['slope']}, "
                              f"interval={result['interval']}, x0={result['x0']}")
                    return result

            except Exception as e:
                if debug:
                    print(f"  Warning: Failed to load device config: {e}")

    if debug:
        print(f"  No device config found, using defaults")
        print(f"  (Install CubeStage to auto-detect calibration)")

    return result
