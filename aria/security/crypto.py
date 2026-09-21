"""Encryption at rest (NFR 002).

ARIA is a local application with no network layer, so "in transit" reduces to
inter process communication on the workstation and there is nothing on a wire to
protect. What genuinely needs protecting is data on disk, and that is what this
module provides.

Design
------
A per installation master key is generated on first run and kept in the
configuration directory with restrictive permissions. Content is encrypted with
AES-256 in GCM, which authenticates as well as encrypts, so a modified
ciphertext is rejected rather than silently decrypted to rubbish.

The master key can be wrapped by a passphrase. When an administrator sets one,
the key file holds only the wrapped key and the passphrase is required at
startup. Without a passphrase the key file is protected by file system
permissions alone, which is honest about what it does and does not defend
against, and the compatibility check reports which mode is in use.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    AESGCM = None
    Scrypt = None
    CRYPTO_AVAILABLE = False

KEY_BYTES = 32
NONCE_BYTES = 12
WRAP_SALT_BYTES = 16
KEY_FILE_VERSION = 1


class VaultError(RuntimeError):
    """Raised for key handling problems, with a message safe to show a user."""


class VaultLockedError(VaultError):
    """Raised when the vault needs a passphrase that has not been supplied."""


@dataclass
class VaultStatus:
    available: bool = False
    initialised: bool = False
    passphrase_protected: bool = False
    unlocked: bool = False
    key_path: str = ""
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "initialised": self.initialised,
            "passphrase_protected": self.passphrase_protected,
            "unlocked": self.unlocked,
            "key_path": self.key_path,
            "message": self.message,
        }


def _restrict_permissions(path: Path) -> None:
    """Limit a key file to the owner on every platform ARIA supports."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    if os.name == "nt":
        # On Windows, remove inherited access and grant the current user only.
        try:
            import subprocess

            user = os.environ.get("USERNAME", "")
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    check=False, capture_output=True, timeout=10,
                )
        except Exception:
            pass


class Vault:
    """Holds the master key and encrypts and decrypts payloads."""

    def __init__(self, key_path):
        self.key_path = Path(key_path)
        self._key: bytes | None = None

    # -- status --------------------------------------------------------------

    def status(self) -> VaultStatus:
        s = VaultStatus(available=CRYPTO_AVAILABLE, key_path=str(self.key_path))
        if not CRYPTO_AVAILABLE:
            s.message = (
                "The cryptography library is not available, so encryption at "
                "rest is switched off. Reinstall ARIA to restore it."
            )
            return s
        if not self.key_path.exists():
            s.message = "No key has been created yet. One is generated on first use."
            return s
        s.initialised = True
        try:
            meta = json.loads(self.key_path.read_text(encoding="utf-8"))
            s.passphrase_protected = bool(meta.get("wrapped"))
        except (OSError, ValueError):
            s.message = "The key file could not be read."
            return s
        s.unlocked = self._key is not None
        s.message = (
            "Encryption is active and the key is unlocked."
            if s.unlocked
            else (
                "A passphrase is required to unlock the key."
                if s.passphrase_protected
                else "Encryption is active."
            )
        )
        return s

    # -- key lifecycle -------------------------------------------------------

    def initialise(self, passphrase: str | None = None) -> None:
        """Create the master key if there is not one already."""
        if not CRYPTO_AVAILABLE:
            raise VaultError(
                "Encryption at rest cannot be enabled because the cryptography "
                "library is not available."
            )
        if self.key_path.exists():
            return
        key = secrets.token_bytes(KEY_BYTES)
        self._write_key_file(key, passphrase)
        self._key = key

    def _write_key_file(self, key: bytes, passphrase: str | None) -> None:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if passphrase:
            salt = secrets.token_bytes(WRAP_SALT_BYTES)
            wrapping_key = self._derive(passphrase, salt)
            nonce = secrets.token_bytes(NONCE_BYTES)
            wrapped = AESGCM(wrapping_key).encrypt(nonce, key, b"aria-master-key")
            payload = {
                "version": KEY_FILE_VERSION,
                "wrapped": True,
                "salt": base64.b64encode(salt).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "key": base64.b64encode(wrapped).decode("ascii"),
            }
        else:
            payload = {
                "version": KEY_FILE_VERSION,
                "wrapped": False,
                "key": base64.b64encode(key).decode("ascii"),
            }
        from ..io.fsutil import atomic_write_text

        atomic_write_text(self.key_path, json.dumps(payload, indent=2))
        _restrict_permissions(self.key_path)

    @staticmethod
    def _derive(passphrase: str, salt: bytes) -> bytes:
        kdf = Scrypt(salt=salt, length=KEY_BYTES, n=2 ** 15, r=8, p=1)
        return kdf.derive(passphrase.encode("utf-8"))

    def unlock(self, passphrase: str | None = None) -> None:
        """Load the master key, deriving the wrapping key when needed."""
        if not CRYPTO_AVAILABLE:
            raise VaultError("The cryptography library is not available.")
        if not self.key_path.exists():
            self.initialise(passphrase)
            return
        try:
            meta = json.loads(self.key_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VaultError(
                f"The encryption key file could not be read: {exc}. Restore it "
                f"from backup, because data encrypted with it cannot be read "
                f"without it."
            ) from exc

        if not meta.get("wrapped"):
            self._key = base64.b64decode(meta["key"])
            return
        if not passphrase:
            raise VaultLockedError(
                "This installation protects its encryption key with a "
                "passphrase. Enter it to continue."
            )
        salt = base64.b64decode(meta["salt"])
        nonce = base64.b64decode(meta["nonce"])
        wrapped = base64.b64decode(meta["key"])
        try:
            self._key = AESGCM(self._derive(passphrase, salt)).decrypt(
                nonce, wrapped, b"aria-master-key"
            )
        except Exception as exc:
            raise VaultError("The passphrase is not correct.") from exc

    def set_passphrase(self, current: str | None, new_passphrase: str | None) -> None:
        """Add, change or remove the passphrase protecting the key."""
        self.unlock(current)
        if self._key is None:
            raise VaultError("The key could not be unlocked.")
        self._write_key_file(self._key, new_passphrase or None)

    @property
    def is_unlocked(self) -> bool:
        return self._key is not None

    def lock(self) -> None:
        self._key = None

    # -- payload encryption --------------------------------------------------

    def encrypt(self, plaintext: bytes, context: bytes = b"aria") -> bytes:
        """Encrypt with a fresh nonce. The nonce is prefixed to the output."""
        if self._key is None:
            raise VaultLockedError("The encryption key is locked.")
        nonce = secrets.token_bytes(NONCE_BYTES)
        return nonce + AESGCM(self._key).encrypt(nonce, plaintext, context)

    def decrypt(self, payload: bytes, context: bytes = b"aria") -> bytes:
        if self._key is None:
            raise VaultLockedError("The encryption key is locked.")
        if len(payload) <= NONCE_BYTES:
            raise VaultError("The encrypted payload is too short to be valid.")
        nonce, body = payload[:NONCE_BYTES], payload[NONCE_BYTES:]
        try:
            return AESGCM(self._key).decrypt(nonce, body, context)
        except Exception as exc:
            raise VaultError(
                "The encrypted content could not be read. It was either "
                "modified or encrypted with a different key."
            ) from exc

    def encrypt_file(self, source, destination, context: bytes = b"aria") -> None:
        from ..io.fsutil import atomic_write_bytes

        atomic_write_bytes(destination, self.encrypt(Path(source).read_bytes(), context))

    def decrypt_file(self, source, destination, context: bytes = b"aria") -> None:
        from ..io.fsutil import atomic_write_bytes

        atomic_write_bytes(destination, self.decrypt(Path(source).read_bytes(), context))


def file_system_encryption_status() -> dict:
    """Report whole disk or folder encryption provided by the operating system.

    Application level encryption and platform encryption solve different parts
    of the problem, so the compatibility check reports both rather than implying
    one replaces the other.
    """
    import sys

    info = {"platform": sys.platform, "detected": "unknown", "detail": ""}
    try:
        if sys.platform.startswith("win"):
            import subprocess

            out = subprocess.run(
                ["manage-bde", "-status", "C:"], capture_output=True, text=True,
                timeout=15, check=False,
            )
            text = (out.stdout or "") + (out.stderr or "")
            if "Percentage Encrypted:  100" in text or "Fully Encrypted" in text:
                info["detected"] = "enabled"
                info["detail"] = "The system volume reports full volume encryption."
            elif "Fully Decrypted" in text or "Percentage Encrypted:  0" in text:
                info["detected"] = "disabled"
                info["detail"] = "The system volume is not encrypted."
            else:
                info["detail"] = "Volume encryption state could not be determined."
        elif sys.platform == "darwin":
            import subprocess

            out = subprocess.run(
                ["fdesetup", "status"], capture_output=True, text=True, timeout=15, check=False
            )
            text = out.stdout or ""
            if "FileVault is On" in text:
                info["detected"] = "enabled"
                info["detail"] = "Full disk encryption is on."
            elif "FileVault is Off" in text:
                info["detected"] = "disabled"
                info["detail"] = "Full disk encryption is off."
    except Exception as exc:
        info["detail"] = f"The check could not be completed: {exc}"
    return info
