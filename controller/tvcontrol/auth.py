import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from .store import Store


class Auth:
    def __init__(self, data_dir: Path, store: Store, password: str | None = None,
                 required: bool = True):
        self.store = store
        self.lock = threading.Lock()
        self.required = required
        self.failures: dict[str, list[float]] = {}
        self.record: dict | None = None
        if not required:
            return
        path = data_dir / "admin-auth.json"
        if password is not None and not 12 <= len(password) <= 1024:
            raise ValueError("TVCONTROL_ADMIN_PASSWORD must contain 12 to 1024 characters")
        if password is not None and path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if hmac.compare_digest(
                    self._hash(password, bytes.fromhex(old["salt"])).hex(), old["hash"]):
                password = None
        if password is not None or not path.exists():
            if password is None:
                password = secrets.token_urlsafe(20)
                secret_path = data_dir / "initial-admin-password.txt"
                secret_path.write_text(password + "\n", encoding="utf-8")
                os.chmod(secret_path, 0o600)
            salt = secrets.token_bytes(16)
            record = {"salt": salt.hex(), "hash": self._hash(password, salt).hex()}
            path.write_text(json.dumps(record), encoding="utf-8")
            os.chmod(path, 0o600)
            store.clear_sessions()
        self.record = json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _hash(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)

    def allowed(self, address: str) -> bool:
        cutoff = time.time() - 300
        self.failures[address] = [t for t in self.failures.get(address, []) if t > cutoff]
        return len(self.failures[address]) < 10

    def login(self, password: str, address: str) -> tuple[str, str] | None:
        if self.record is None:
            raise ValueError("Password login is disabled")
        actual = self._hash(password, bytes.fromhex(self.record["salt"])).hex()
        if not hmac.compare_digest(actual, self.record["hash"]):
            self.failures.setdefault(address, []).append(time.time())
            return None
        self.failures.pop(address, None)
        return self.new_session(authenticated=True)

    def new_session(self, *, authenticated: bool) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.store.create_session(self.token_hash(token), csrf, time.time() + 8 * 3600,
                                  authenticated=authenticated)
        return token, csrf

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, token: str | None) -> dict | None:
        current = self.store.session(self.token_hash(token), time.time()) if token else None
        if current is not None and self.required and not current["authenticated"]:
            return None
        return current
