"""Explicit transaction ownership with nested SQLite savepoints."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .database import DatabaseManager
from .errors import DatabaseConnectionError, TransactionError


@dataclass(slots=True)
class _TransactionState:
    connection: sqlite3.Connection
    depth: int = 0
    savepoint_counter: int = 0
    manager_control: bool = False


class TransactionManager:
    """Own explicit transactions and translate nesting into savepoints.

    Repository code may execute SQL but may not issue ``BEGIN``, ``COMMIT``,
    ``ROLLBACK``, or savepoint control. The manager's SQLite authorizer rejects
    those operations, preventing hidden commits and partial transaction ownership.
    """

    def __init__(self, database: DatabaseManager) -> None:
        self._database = database
        self._state: ContextVar[_TransactionState | None] = ContextVar(
            f"lim_transaction_state_{id(self)}", default=None
        )

    @property
    def database(self) -> DatabaseManager:
        """Return the injected database dependency for composition validation."""
        return self._database

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a managed connection, committing or rolling back atomically."""
        state = self._state.get()
        if state is not None:
            with self._nested_transaction(state) as connection:
                yield connection
            return

        try:
            with self._database.connection() as connection:
                state = _TransactionState(connection=connection)
                token = self._state.set(state)
                connection.set_authorizer(
                    lambda action, arg1, _arg2, _database, _source: self._authorize(
                        state, action, arg1
                    )
                )
                try:
                    self._control(
                        state,
                        f"BEGIN {self._database.settings.transaction_mode}",
                    )
                    state.depth = 1
                    try:
                        yield connection
                    except BaseException as exc:
                        self._rollback_outer(state, exc)
                    else:
                        if not connection.in_transaction:
                            raise TransactionError(
                                "transaction ended outside TransactionManager"
                            )
                        self._control(state, "COMMIT")
                finally:
                    connection.set_authorizer(None)
                    self._state.reset(token)
        except DatabaseConnectionError:
            raise
        except TransactionError:
            raise
        except sqlite3.Error as exc:
            raise TransactionError(
                f"transaction failed due to {type(exc).__name__}"
            ) from exc

    @contextmanager
    def _nested_transaction(
        self, state: _TransactionState
    ) -> Iterator[sqlite3.Connection]:
        if not state.connection.in_transaction:
            raise TransactionError("cannot nest outside an active transaction")
        state.savepoint_counter += 1
        savepoint = f"lim_sp_{state.savepoint_counter}"
        self._control(state, f"SAVEPOINT {savepoint}")
        state.depth += 1
        try:
            yield state.connection
        except BaseException as exc:
            try:
                self._control(state, f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._control(state, f"RELEASE SAVEPOINT {savepoint}")
            except sqlite3.Error as rollback_error:
                raise TransactionError(
                    "nested transaction rollback failed"
                ) from rollback_error
            raise exc
        else:
            try:
                self._control(state, f"RELEASE SAVEPOINT {savepoint}")
            except sqlite3.Error as exc:
                raise TransactionError("nested transaction commit failed") from exc
        finally:
            state.depth -= 1

    def _rollback_outer(
        self,
        state: _TransactionState,
        original_error: BaseException,
    ) -> None:
        try:
            if state.connection.in_transaction:
                self._control(state, "ROLLBACK")
        except sqlite3.Error as exc:
            raise TransactionError("transaction rollback failed") from exc
        if isinstance(original_error, sqlite3.Error):
            raise TransactionError(
                f"transaction failed due to {type(original_error).__name__}"
            ) from original_error
        raise original_error

    @staticmethod
    def _authorize(
        state: _TransactionState,
        action: int,
        _argument: str | None,
    ) -> int:
        transaction_actions = {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}
        if action in transaction_actions and not state.manager_control:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @staticmethod
    def _control(state: _TransactionState, statement: str) -> None:
        state.manager_control = True
        try:
            state.connection.execute(statement)
        finally:
            state.manager_control = False
