"""Schema migration for normalized discovery observation history."""

from __future__ import annotations

import sqlite3

from .migrations import Migration


def _create_discovery_schema(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE discovery_observations (
            uuid TEXT PRIMARY KEY,
            server_uuid TEXT NOT NULL REFERENCES inventory_servers(uuid),
            source TEXT NOT NULL
                CHECK(source IN ('manual','ssh','plugin','import','other')),
            state TEXT NOT NULL
                CHECK(state IN ('pending','successful','failed','expired')),
            status TEXT NOT NULL
                CHECK(status IN ('unknown','complete','partial','failed')),
            synchronization_state TEXT NOT NULL
                CHECK(synchronization_state IN
                    ('pending','accepted','rejected','conflict')),
            discovered_at TEXT NOT NULL,
            collection_duration_ms INTEGER NOT NULL
                CHECK(collection_duration_ms >= 0),
            collector_version TEXT NOT NULL, hostname TEXT NOT NULL COLLATE NOCASE,
            fqdn TEXT COLLATE NOCASE, os_name TEXT,
            os_distribution TEXT, os_version TEXT,
            kernel_name TEXT, kernel_version TEXT, architecture TEXT,
            cpu_model TEXT, cpu_logical_cores INTEGER CHECK(cpu_logical_cores >= 0),
            cpu_physical_cores INTEGER CHECK(cpu_physical_cores >= 0),
            memory_total_bytes INTEGER CHECK(memory_total_bytes >= 0),
            memory_available_bytes INTEGER
                CHECK(memory_available_bytes >= 0
                    AND memory_available_bytes <= memory_total_bytes),
            network_domain TEXT, default_gateway TEXT, notes TEXT, failure_reason TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version >= 1),
            CHECK((state = 'failed' AND status = 'failed'
                    AND failure_reason IS NOT NULL)
               OR (state IN ('pending','successful') AND failure_reason IS NULL)
               OR state = 'expired'),
            CHECK((state = 'pending' AND status IN ('unknown','partial'))
               OR (state = 'successful' AND status IN ('complete','partial'))
               OR (state = 'failed' AND status = 'failed')
               OR (state = 'expired'
                   AND status IN ('complete','partial','failed')))
        )""",
        """CREATE TABLE discovery_interfaces (
            id INTEGER PRIMARY KEY,
            observation_uuid TEXT NOT NULL
                REFERENCES discovery_observations(uuid) ON DELETE CASCADE,
            name TEXT NOT NULL, mac_address TEXT,
            is_up INTEGER CHECK(is_up IN (0,1)), mtu INTEGER CHECK(mtu >= 0),
            UNIQUE(observation_uuid, name)
        )""",
        """CREATE TABLE discovery_addresses (
            id INTEGER PRIMARY KEY,
            observation_uuid TEXT NOT NULL
                REFERENCES discovery_observations(uuid) ON DELETE CASCADE,
            address TEXT NOT NULL, interface_name TEXT, kind TEXT NOT NULL,
            UNIQUE(observation_uuid, address, kind)
        )""",
        """CREATE TABLE discovery_disks (
            id INTEGER PRIMARY KEY,
            observation_uuid TEXT NOT NULL
                REFERENCES discovery_observations(uuid) ON DELETE CASCADE,
            name TEXT NOT NULL, total_bytes INTEGER NOT NULL CHECK(total_bytes >= 0),
            available_bytes INTEGER
                CHECK(available_bytes >= 0 AND available_bytes <= total_bytes),
            mount_point TEXT, filesystem TEXT,
            UNIQUE(observation_uuid, name, mount_point)
        )""",
        """CREATE TABLE discovery_services (
            id INTEGER PRIMARY KEY,
            observation_uuid TEXT NOT NULL
                REFERENCES discovery_observations(uuid) ON DELETE CASCADE,
            name TEXT NOT NULL, status TEXT NOT NULL, version TEXT,
            port INTEGER CHECK(port BETWEEN 1 AND 65535),
            UNIQUE(observation_uuid, name, port)
        )""",
        """CREATE TABLE discovery_packages (
            id INTEGER PRIMARY KEY,
            observation_uuid TEXT NOT NULL
                REFERENCES discovery_observations(uuid) ON DELETE CASCADE,
            name TEXT NOT NULL, version TEXT NOT NULL, manager TEXT,
            UNIQUE(observation_uuid, name, version, manager)
        )""",
        """CREATE TABLE discovery_containers (
            id INTEGER PRIMARY KEY,
            observation_uuid TEXT NOT NULL
                REFERENCES discovery_observations(uuid) ON DELETE CASCADE,
            identifier TEXT NOT NULL, name TEXT NOT NULL,
            image TEXT NOT NULL, status TEXT NOT NULL,
            UNIQUE(observation_uuid, identifier)
        )""",
        """CREATE TABLE discovery_processes (
            id INTEGER PRIMARY KEY,
            observation_uuid TEXT NOT NULL
                REFERENCES discovery_observations(uuid) ON DELETE CASCADE,
            pid INTEGER NOT NULL CHECK(pid >= 0), name TEXT NOT NULL,
            UNIQUE(observation_uuid, pid)
        )""",
        """CREATE TABLE discovery_metadata (
            observation_uuid TEXT NOT NULL
                REFERENCES discovery_observations(uuid) ON DELETE CASCADE,
            namespace TEXT NOT NULL
                CHECK(namespace IN
                    ('docker','redis','mysql','freepbx','prometheus','raw')),
            key TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY(observation_uuid, namespace, key)
        )""",
        "CREATE INDEX idx_discovery_observations_server_time "
        "ON discovery_observations(server_uuid, discovered_at DESC)",
        "CREATE INDEX idx_discovery_observations_source_time "
        "ON discovery_observations(source, discovered_at DESC)",
        "CREATE INDEX idx_discovery_observations_status "
        "ON discovery_observations(status, discovered_at DESC)",
        "CREATE INDEX idx_discovery_observations_state_updated "
        "ON discovery_observations(state, updated_at)",
        "CREATE INDEX idx_discovery_addresses_observation "
        "ON discovery_addresses(observation_uuid)",
        "CREATE INDEX idx_discovery_addresses_address ON discovery_addresses(address)",
        "CREATE INDEX idx_discovery_services_observation "
        "ON discovery_services(observation_uuid)",
        "CREATE INDEX idx_discovery_packages_observation "
        "ON discovery_packages(observation_uuid)",
        "CREATE INDEX idx_discovery_containers_observation "
        "ON discovery_containers(observation_uuid)",
        "CREATE INDEX idx_discovery_metadata_lookup "
        "ON discovery_metadata(namespace, key)",
    )
    for statement in statements:
        connection.execute(statement)


DISCOVERY_MIGRATION = Migration(3, "create_discovery_schema", _create_discovery_schema)
