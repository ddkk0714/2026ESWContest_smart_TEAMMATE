"""Host-only Mock write-ahead command ledger. Never imported by the Pi4 Hub.

Persist the claim before any adapter operation and the intent before POST.
An unfinished claim is recovered by readback only; POST is never retried.
One gateway owns a journal at a time. A journal error fails closed.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sqlite3
from typing import Any


class JournalError(RuntimeError):
    pass


class CommandJournal:
    def __init__(self, path: str | Path, mode: str = 'mock') -> None:
        if mode != 'mock':
            raise JournalError('journal_is_mock_only_in_this_milestone')
        self._lock = None
        self._db = None
        try:
            path = Path(path)
            self._lock = os.fdopen(os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600), 'a+')
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
            self._db = sqlite3.connect(path)
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self._db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('mode', mode))
            self._db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, command TEXT NOT NULL, record TEXT NOT NULL)')
            if self._db.execute('SELECT value FROM metadata WHERE name="mode"').fetchone() != (mode,):
                raise JournalError('journal_mode_mismatch')
            self._db.commit()
        except Exception:
            self.close()
            raise JournalError('journal_unavailable_or_incompatible') from None

    def load(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        try:
            return [(json.loads(command), json.loads(record)) for command, record in
                    self._db.execute('SELECT command, record FROM commands ORDER BY rowid')]
        except Exception:
            raise JournalError('journal_read_failed') from None

    def claim(self, command: dict[str, Any], evidence: dict[str, Any]) -> None:
        try:
            with self._db:
                self._db.execute('INSERT INTO commands VALUES (?, ?, ?)',
                    (command['command_id'], json.dumps(command, sort_keys=True, allow_nan=False),
                     json.dumps({'phase': 'claimed', 'evidence': evidence}, allow_nan=False)))
        except Exception:
            raise JournalError('journal_claim_failed') from None

    def save(self, command_id: str, record: dict[str, Any]) -> None:
        try:
            with self._db:
                cursor = self._db.execute('UPDATE commands SET record=? WHERE id=?',
                                         (json.dumps(record, allow_nan=False), command_id))
                if cursor.rowcount != 1:
                    raise JournalError('unknown_journal_command')
        except Exception:
            raise JournalError('journal_write_failed') from None

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None
