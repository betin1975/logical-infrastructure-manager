"""Versioned SQLite schema migration for authoritative inventory state."""

from __future__ import annotations

import sqlite3

from .migrations import Migration

_INVENTORY_SCHEMA = """
        CREATE TABLE inventory_servers (
            uuid TEXT PRIMARY KEY,
            hostname TEXT NOT NULL COLLATE NOCASE UNIQUE,
            display_name TEXT NOT NULL,
            platform TEXT NOT NULL
                CHECK (platform IN ('unknown', 'bare_metal', 'virtual_machine',
                    'cloud', 'container', 'network', 'appliance')),
            operating_system TEXT NOT NULL
                CHECK (operating_system IN ('unknown', 'linux', 'windows',
                    'macos', 'freebsd', 'network_os', 'other')),
            distribution TEXT,
            distribution_version TEXT,
            kernel_version TEXT,
            architecture TEXT,
            server_type TEXT NOT NULL
                CHECK (server_type IN ('unknown', 'physical', 'virtual_machine',
                    'cloud_instance', 'container_host', 'network_device',
                    'appliance')),
            environment TEXT,
            location TEXT,
            description TEXT,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            managed INTEGER NOT NULL CHECK (managed IN (0, 1)),
            discovery_state TEXT NOT NULL
                CHECK (discovery_state IN ('unknown', 'discovered', 'missing')),
            health_status TEXT NOT NULL
                CHECK (health_status IN ('unknown', 'healthy', 'unhealthy')),
            status TEXT NOT NULL
                CHECK (status IN ('active', 'disabled', 'missing', 'deleted')),
            last_poll_at TEXT,
            last_successful_poll_at TEXT,
            last_failure_at TEXT,
            failure_count INTEGER NOT NULL CHECK (failure_count >= 0),
            last_bootstrap_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            synchronization_state TEXT NOT NULL
                CHECK (synchronization_state IN
                    ('pending', 'in_sync', 'error', 'conflict')),
            inventory_version INTEGER NOT NULL CHECK (inventory_version > 0),
            notes TEXT,
            CHECK (updated_at >= created_at),
            CHECK (
                (status = 'deleted' AND deleted_at IS NOT NULL AND enabled = 0)
                OR (status <> 'deleted' AND deleted_at IS NULL)
            ),
            CHECK (status <> 'active' OR enabled = 1),
            CHECK (status <> 'disabled' OR enabled = 0)
        );

        CREATE TABLE inventory_server_addresses (
            address TEXT PRIMARY KEY,
            server_uuid TEXT NOT NULL
                REFERENCES inventory_servers(uuid) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('primary', 'management')),
            UNIQUE (server_uuid, kind)
        );

        CREATE TABLE inventory_tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE
        );

        CREATE TABLE inventory_server_tags (
            server_uuid TEXT NOT NULL
                REFERENCES inventory_servers(uuid) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL
                REFERENCES inventory_tags(id) ON DELETE CASCADE,
            PRIMARY KEY (server_uuid, tag_id)
        );

        CREATE TABLE inventory_server_labels (
            server_uuid TEXT NOT NULL
                REFERENCES inventory_servers(uuid) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (server_uuid, key)
        );

        CREATE INDEX idx_inventory_servers_enabled
            ON inventory_servers(enabled, hostname)
            WHERE deleted_at IS NULL;
        CREATE INDEX idx_inventory_servers_managed
            ON inventory_servers(managed, hostname)
            WHERE deleted_at IS NULL;
        CREATE INDEX idx_inventory_servers_health
            ON inventory_servers(health_status, hostname)
            WHERE deleted_at IS NULL;
        CREATE INDEX idx_inventory_servers_status
            ON inventory_servers(status, hostname);
        CREATE INDEX idx_inventory_addresses_server
            ON inventory_server_addresses(server_uuid, kind);
        CREATE INDEX idx_inventory_server_tags_tag
            ON inventory_server_tags(tag_id, server_uuid);
        CREATE INDEX idx_inventory_labels_key_value
            ON inventory_server_labels(key, value, server_uuid);
        """


def _create_inventory_schema(connection: sqlite3.Connection) -> None:
    # ``executescript`` performs implicit transaction control. Executing these
    # fixed statements individually preserves MigrationManager's atomic boundary.
    for statement in _INVENTORY_SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)


INVENTORY_MIGRATION = Migration(2, "create_inventory_schema", _create_inventory_schema)
