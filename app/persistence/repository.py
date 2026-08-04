"""Minimal repository contracts for future LIM domain persistence."""

from __future__ import annotations

import sqlite3
from typing import Protocol, runtime_checkable


@runtime_checkable
class Repository(Protocol):
    """Contract for repositories bound to an injected managed transaction.

    Repositories are the only classes allowed to read or persist domain state.
    They execute parameterized SQL through ``connection`` and never construct a
    ``DatabaseManager`` or commit, roll back, or migrate. Business logic,
    ``SSHManager``, plugins, and jobs depend on repository interfaces and never
    import SQLite or execute SQL.
    """

    connection: sqlite3.Connection


class BaseRepository:
    """Small base for repositories that receive their connection explicitly."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("repository connection must be sqlite3.Connection")
        self.connection = connection
