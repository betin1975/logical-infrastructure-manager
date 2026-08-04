"""SQL-free repository interfaces owned by the inventory domain."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from .models import HealthStatus, RepositoryResult, Server


@runtime_checkable
class InventoryRepository(Protocol):
    """Persistence contract consumed by :class:`InventoryService`."""

    def create(self, server: Server) -> Server:
        """Persist a new authoritative server."""

    def update(self, server: Server) -> Server:
        """Persist a new optimistic version of an active server."""

    def delete(self, server: Server) -> Server:
        """Persist a domain-approved soft deletion."""

    def restore(self, server: Server) -> Server:
        """Persist a domain-approved restoration."""

    def find_by_uuid(
        self,
        server_uuid: UUID,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        """Find one server by UUID."""

    def find_by_hostname(
        self,
        hostname: str,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        """Find one server by normalized hostname."""

    def find_by_address(
        self,
        address: str,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        """Find one server by primary or management address."""

    def find_enabled(
        self, *, limit: int = 100, offset: int = 0
    ) -> RepositoryResult[Server]:
        """Return enabled, non-deleted servers."""

    def find_managed(
        self, *, limit: int = 100, offset: int = 0
    ) -> RepositoryResult[Server]:
        """Return managed, non-deleted servers."""

    def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> RepositoryResult[Server]:
        """Return a stable page of inventory servers."""

    def search(
        self,
        query: str,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> RepositoryResult[Server]:
        """Search normalized inventory fields."""

    def count(self, *, include_deleted: bool = False) -> int:
        """Count inventory servers."""

    def find_by_tag(
        self,
        tag: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RepositoryResult[Server]:
        """Return non-deleted servers carrying a tag."""

    def find_by_health(
        self,
        health_status: HealthStatus,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RepositoryResult[Server]:
        """Return non-deleted servers with a health state."""
