"""Minimal repository contracts for future LIM domain persistence."""

from __future__ import annotations

import sqlite3
from typing import Protocol, runtime_checkable


@runtime_checkable
class Repository(Protocol):
    """Contract for repositories bound to an injected managed transaction.

    Repositories execute parameterized SQL through ``connection``. They never
    construct a ``DatabaseManager`` and never commit, roll back, or migrate.
    """

    connection: sqlite3.Connection


class BaseRepository:
    """Small base for repositories that receive their connection explicitly."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("repository connection must be sqlite3.Connection")
        self.connection = connection
