"""Backup and restore service.

Operates at the installation level, not per-company: this system uses a
single shared database file holding every tenant company's data (one
desktop installation commonly serves several companies, e.g. an
accounting firm managing multiple clients), so a backup is a snapshot
of the whole file, not one company's slice of it.

Only SQLite is implemented directly — it is this project's default and
primary supported backend. Backups use SQLite's own online-backup API
(:meth:`sqlite3.Connection.backup`) rather than a raw file copy: this
project's :class:`~database.database.Database` enables WAL journal
mode, under which recently committed transactions can sit in a
separate ``-wal`` sidecar file that has not yet been checkpointed into
the main ``.db`` file — a plain ``shutil.copy`` of just the ``.db``
file can silently produce a backup missing those commits. SQLite's
backup API is specifically designed to produce a complete, consistent
snapshot regardless of WAL/checkpoint state, even while the source
database is open elsewhere.

Every backup file is encrypted at rest (see :mod:`utils.encryption`,
the same per-installation key used for encrypted columns): the online
-backup API first produces a plain, consistent snapshot in a private
temporary file, which is then encrypted into the real
``backup_*.db.enc`` file and immediately deleted — a plaintext copy of
the whole database never sits in :attr:`~config.PathsConfig.backups_dir`
even momentarily. Restoring reverses this: decrypt to a private
temporary file, then run the same online-backup copy from that
temporary file into the live database path.

PostgreSQL/MySQL backups require their own external dump tools
(``pg_dump``/``mysqldump``), which is out of scope for this in-process
service; :meth:`BackupService.create_backup` raises
:class:`NotImplementedError` for those dialects rather than silently
doing nothing.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config import DatabaseDialect, get_config
from database.database import Database, get_database
from utils.encryption import decrypt_file, encrypt_file
from utils.logger import logger


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    """Copy a SQLite database file via the online-backup API.

    Correct even while ``source_path`` is open elsewhere and has
    uncheckpointed WAL data, unlike a raw file copy.

    Args:
        source_path: The database file to read from.
        destination_path: The database file to write (created or
            overwritten).
    """
    source_connection = sqlite3.connect(str(source_path))
    try:
        destination_connection = sqlite3.connect(str(destination_path))
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    finally:
        source_connection.close()


class BackupService:
    """Create, restore, list, and prune database backups.

    Attributes:
        database: The :class:`~database.database.Database` instance
            whose underlying file this service backs up/restores.
    """

    def __init__(self, database: Database | None = None) -> None:
        """Create a backup service.

        Args:
            database: The database to operate on; defaults to the
                process-wide :func:`~database.database.get_database`
                singleton.
        """
        self.database = database or get_database()

    def create_backup(self, *, label: str | None = None) -> Path:
        """Create a timestamped copy of the live database file.

        Args:
            label: Optional short label appended to the filename (e.g.
                ``"pre_upgrade"``), sanitized to word characters only.

        Returns:
            The path to the newly created backup file.

        Raises:
            NotImplementedError: If the configured database dialect is
                not SQLite.
            FileNotFoundError: If the live database file does not exist.
        """
        config = get_config()
        if config.database.dialect is not DatabaseDialect.SQLITE:
            raise NotImplementedError(
                f"Automatic backup is only implemented for SQLite in this "
                f"version; dialect={config.database.dialect.value!r} requires "
                f"an external dump tool (pg_dump/mysqldump)."
            )

        source_path = config.database.sqlite_path
        if not source_path.exists():
            raise FileNotFoundError(f"Database file not found: {source_path}")

        config.paths.backups_dir.mkdir(parents=True, exist_ok=True)
        # Microsecond precision: two backups triggered within the same
        # second (e.g. a script calling create_backup() in a tight loop)
        # would otherwise collide on an identical filename and silently
        # overwrite each other with no error.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        suffix = f"_{_sanitize_label(label)}" if label else ""
        backup_path = config.paths.backups_dir / f"backup_{timestamp}{suffix}.db.enc"

        # The online-backup API needs a real file to write the consistent
        # snapshot into before it can be encrypted; that plaintext copy
        # lives only in a private temp file for the few moments between
        # these two calls, then is deleted unconditionally.
        temp_fd, temp_name = tempfile.mkstemp(suffix=".db.tmp")
        os.close(temp_fd)
        temp_path = Path(temp_name)
        try:
            _sqlite_backup(source_path, temp_path)
            encrypt_file(temp_path, backup_path)
        finally:
            temp_path.unlink(missing_ok=True)

        logger.info("Encrypted backup created: {path}", path=str(backup_path))
        return backup_path

    def restore_backup(self, backup_path: Path) -> None:
        """Restore the database from a backup file, overwriting the live file.

        Disposes the bound :class:`~database.database.Database`'s
        connection pool first, releasing every pooled connection so the
        live file is not open (and thus not locked, for SQLite) while
        it is overwritten. Any session created before this call must be
        discarded by the caller — this operation invalidates it.

        Args:
            backup_path: The backup file to restore from (typically one
                returned by :meth:`create_backup` or found via
                :meth:`list_backups`).

        Raises:
            NotImplementedError: If the configured database dialect is
                not SQLite.
            FileNotFoundError: If ``backup_path`` does not exist.
        """
        config = get_config()
        if config.database.dialect is not DatabaseDialect.SQLITE:
            raise NotImplementedError(
                f"Automatic restore is only implemented for SQLite in this "
                f"version; dialect={config.database.dialect.value!r} requires "
                f"an external restore tool."
            )
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        temp_fd, temp_name = tempfile.mkstemp(suffix=".db.tmp")
        os.close(temp_fd)
        temp_path = Path(temp_name)
        try:
            decrypt_file(backup_path, temp_path)
            self.database.dispose()
            _sqlite_backup(temp_path, config.database.sqlite_path)
        finally:
            temp_path.unlink(missing_ok=True)
        logger.warning("Database restored from backup: {path}", path=str(backup_path))

    def list_backups(self) -> list[Path]:
        """List every existing backup file, most recent first.

        Sorted by the ``YYYYMMDD_HHMMSS`` timestamp embedded in each
        backup's filename (which sorts correctly as a plain string),
        not by filesystem modification time — an mtime-based sort is
        unreliable here because a copied file's mtime can be tied to
        when the *source* was last written rather than when the copy
        was made, depending on the copy method.

        Returns:
            Backup file paths, most recently created first.
        """
        backups_dir = get_config().paths.backups_dir
        if not backups_dir.exists():
            return []
        return sorted(backups_dir.glob("backup_*.db.enc"), key=lambda p: p.name, reverse=True)

    def apply_retention_policy(self, *, retention_count: int | None = None) -> list[Path]:
        """Delete backups beyond the configured retention count.

        Args:
            retention_count: How many most-recent backups to keep;
                defaults to :attr:`config.BackupConfig.retention_count`.

        Returns:
            The paths that were deleted.
        """
        resolved_count = (
            retention_count
            if retention_count is not None
            else get_config().backup.retention_count
        )
        backups = self.list_backups()
        stale_backups = backups[resolved_count:] if resolved_count >= 0 else []
        for stale_backup in stale_backups:
            stale_backup.unlink()
            logger.info("Pruned stale backup: {path}", path=str(stale_backup))
        return stale_backups


def _sanitize_label(label: str) -> str:
    """Reduce a backup label to safe filename characters."""
    return "".join(character if character.isalnum() else "_" for character in label)
