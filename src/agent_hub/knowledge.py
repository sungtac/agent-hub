"""Owner-only, scope-isolated knowledge with version and deletion receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .file_lock import exclusive_lock


SCOPES = {"company", "personal", "shared", "quarantine"}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeStore:
    """Local knowledge; every search is intentionally constrained to one scope."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve(strict=False)
        if not self.root.is_dir():
            raise ValueError("Knowledge root must already exist.")
        self.index_path = self.root / "index.sqlite3"
        # One store-wide advisory lock serialises every read-modify-write so a
        # mutating op cannot clobber a concurrent one or desync the .md files
        # against the sqlite index.
        self.lock_path = self.root / ".knowledge.lock"
        self._initialise_index()

    def save(self, scope: str, identifier: str, content: str) -> Path:
        self._validate(scope, identifier, content)
        with exclusive_lock(self.lock_path):
            directory = self.root / scope / identifier
            if directory.exists():
                raise ValueError("Knowledge item already exists; use update.")
            directory.mkdir(parents=True)
            return self._write_version(scope, identifier, content, 1)

    def update(self, scope: str, identifier: str, content: str) -> Path:
        self._validate(scope, identifier, content)
        with exclusive_lock(self.lock_path):
            directory = self.root / scope / identifier
            if not directory.is_dir() or (directory / "tombstone.json").exists():
                raise ValueError("Active knowledge item does not exist.")
            versions = self._versions(directory)
            if not versions:
                raise ValueError("Knowledge item has no active version.")
            return self._write_version(scope, identifier, content, max(versions) + 1)

    def promote(self, source_scope: str, identifier: str, target_scope: str) -> Path:
        """Explicit owner action; no automatic crossing between knowledge scopes."""
        if target_scope not in SCOPES:
            raise ValueError("Invalid target scope.")
        body = self._content_body(self.read(source_scope, identifier))
        return self.save(target_scope, identifier, body)

    def delete(self, scope: str, identifier: str) -> Path:
        self._validate(scope, identifier, "placeholder")
        with exclusive_lock(self.lock_path):
            directory = self.root / scope / identifier
            document = self._latest_document(directory)
            if document is None:
                raise ValueError("Knowledge item does not exist.")
            digest = hashlib.sha256(document.read_bytes()).hexdigest()
            tombstone = directory / "tombstone.json"
            if tombstone.exists():
                raise ValueError("Knowledge item is already deleted.")
            self._atomic_write(
                tombstone,
                json.dumps({"scope": scope, "id": identifier, "deleted_at": now(), "content_sha256": digest}, ensure_ascii=False, indent=2) + "\n",
            )
            for version in self._versions(directory):
                source = directory / f"v{version}.md"
                source.rename(directory / f"v{version}.deleted")
            with self._connection() as connection:
                connection.execute("UPDATE knowledge SET status = 'deleted', content = '' WHERE scope = ? AND identifier = ?", (scope, identifier))
            return tombstone

    def read(self, scope: str, identifier: str) -> str:
        self._validate(scope, identifier, "placeholder")
        directory = self.root / scope / identifier
        if (directory / "tombstone.json").exists():
            raise PermissionError("Deleted knowledge is unavailable.")
        document = self._latest_document(directory)
        if document is None:
            raise ValueError("Knowledge item does not exist.")
        return document.read_text(encoding="utf-8")

    def search(self, scope: str, query: str, limit: int = 5) -> list[dict[str, object]]:
        if scope not in SCOPES or not query.strip() or not 1 <= limit <= 20:
            raise ValueError("Invalid search scope, query, or limit.")
        terms = [term for term in query.casefold().split() if term]
        where = " AND ".join("lower(content) LIKE ?" for _ in terms)
        parameters: list[object] = [scope, *[f"%{term}%" for term in terms], limit]
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT identifier, version, updated_at FROM knowledge WHERE scope = ? AND status = 'active' AND {where} ORDER BY updated_at DESC LIMIT ?",  # noqa: S608 - fixed fragments
                parameters,
            ).fetchall()
        return [{"id": row[0], "version": row[1], "updated_at": row[2]} for row in rows]

    def _write_version(self, scope: str, identifier: str, content: str, version: int) -> Path:
        directory = self.root / scope / identifier
        document = directory / f"v{version}.md"
        timestamp = now()
        self._atomic_write(
            document,
            f"---\nid: {identifier}\nscope: {scope}\nversion: {version}\nstatus: active\nupdated_at: {timestamp}\n---\n\n{content.strip()}\n",
        )
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO knowledge(scope, identifier, version, status, content, updated_at) VALUES (?, ?, ?, 'active', ?, ?) "
                "ON CONFLICT(scope, identifier) DO UPDATE SET version = excluded.version, status = excluded.status, content = excluded.content, updated_at = excluded.updated_at",
                (scope, identifier, version, content.strip(), timestamp),
            )
        return document

    def _initialise_index(self) -> None:
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS knowledge (scope TEXT NOT NULL, identifier TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(scope, identifier))"
            )
        if os.name != "nt":
            self.index_path.chmod(0o600)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit on success and always release the database handle.

        SQLite's connection context manager does not close the connection.
        Keeping it open is usually harmless on POSIX, but prevents temporary
        knowledge directories from being removed on Windows.
        """

        connection = sqlite3.connect(self.index_path)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _atomic_write(destination: Path, text: str) -> None:
        """Write via a same-directory temp file and os.replace so a crash can
        never leave a truncated document behind."""
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _validate(scope: str, identifier: str, content: str) -> None:
        if scope not in SCOPES or not _IDENTIFIER.fullmatch(identifier) or not content.strip():
            raise ValueError("Invalid knowledge scope, identifier, or content.")

    @staticmethod
    def _versions(directory: Path) -> list[int]:
        return sorted(int(path.stem[1:]) for path in directory.glob("v*.md") if path.stem[1:].isdigit())

    def _latest_document(self, directory: Path) -> Path | None:
        versions = self._versions(directory)
        return directory / f"v{max(versions)}.md" if versions else None

    @staticmethod
    def _content_body(document: str) -> str:
        marker = "---\n\n"
        if marker not in document:
            raise ValueError("Knowledge document has invalid front matter.")
        return document.split(marker, 1)[1].strip()
