"""Encrypted, retained database backups."""

import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy.engine import URL, make_url

from khata_bot.config import Settings

def _fernet(settings: Settings) -> Fernet:
    return Fernet(settings.backup_encryption_key.encode("ascii"))


def _postgres_environment(url: URL) -> dict[str, str]:
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    ssl_mode = url.query.get("sslmode")
    if isinstance(ssl_mode, str):
        env["PGSSLMODE"] = ssl_mode
    return env


def _pg_command(url: URL, command: str) -> list[str]:
    if not url.database:
        raise ValueError("PostgreSQL DATABASE_URL must include a database name.")
    result = [command]
    if url.host:
        result.extend(["--host", url.host])
    if url.port:
        result.extend(["--port", str(url.port)])
    if url.username:
        result.extend(["--username", url.username])
    result.extend(["--dbname", url.database])
    return result


def _database_bytes(database_url: str) -> bytes:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError("Encrypted backups need a file-based SQLite database.")
        with tempfile.TemporaryDirectory(prefix="khata-backup-") as directory:
            snapshot = Path(directory) / "snapshot.db"
            with closing(sqlite3.connect(url.database)) as source, closing(
                sqlite3.connect(snapshot)
            ) as destination:
                source.backup(destination)
            return snapshot.read_bytes()
    if url.get_backend_name() == "postgresql":
        result = subprocess.run(
            _pg_command(url, "pg_dump") + ["--no-owner", "--no-acl", "--format=plain"],
            env=_postgres_environment(url),
            capture_output=True,
            check=False,
            timeout=300,
        )
        if result.returncode:
            raise RuntimeError(
                f"pg_dump failed with exit code {result.returncode}; "
                "check PostgreSQL client tools and database connectivity."
            )
        return result.stdout
    raise ValueError(f"Encrypted backups do not support {url.get_backend_name()}.")


def create_encrypted_backup(settings: Settings, backup_dir: str | Path = "backups") -> Path:
    destination = Path(backup_dir)
    destination.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    backup_path = destination / f"khata-{now.strftime('%Y%m%d-%H%M%S')}.backup.fernet"
    plaintext = _database_bytes(settings.database_url)
    encrypted = _fernet(settings).encrypt(plaintext)
    backup_path.write_bytes(encrypted)
    remove_expired_backups(destination, now)
    return backup_path


def remove_expired_backups(
    backup_dir: str | Path, now: datetime | None = None, retention_days: int = 14
) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    removed = 0
    for path in Path(backup_dir).glob("khata-*.backup.fernet"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            path.unlink()
            removed += 1
    return removed


def restore_encrypted_backup(
    settings: Settings,
    backup_path: str | Path,
    restore_to: str | Path | None = None,
) -> None:
    """Restore a verified encrypted backup over the configured PostgreSQL DB or SQLite file."""
    decrypted = _fernet(settings).decrypt(Path(backup_path).read_bytes())
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        target = Path(restore_to) if restore_to else Path(url.database or "")
        if not str(target):
            raise ValueError("Restore destination is required.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="khata-restore-") as directory:
            snapshot = Path(directory) / "snapshot.db"
            snapshot.write_bytes(decrypted)
            with closing(sqlite3.connect(snapshot)) as source, closing(
                sqlite3.connect(target)
            ) as destination:
                source.backup(destination)
        return
    if url.get_backend_name() == "postgresql":
        result = subprocess.run(
            _pg_command(url, "psql") + ["--set", "ON_ERROR_STOP=1"],
            env=_postgres_environment(url),
            input=decrypted,
            capture_output=True,
            check=False,
            timeout=600,
        )
        if result.returncode:
            raise RuntimeError(
                f"PostgreSQL restore failed with exit code {result.returncode}; "
                "check the target database and PostgreSQL client tools."
            )
        return
    raise ValueError(f"Restore does not support {url.get_backend_name()}.")
