# Plugins

This directory is reserved for LIM provider plugins. Provider names currently
present in the working tree are planning placeholders, not implemented or
supported integrations.

Do not add provider behavior until the versioned plugin manifest, typed plugin
contract, and contract-test harness described in `ARCHITECTURE.md` are approved.
Plugins must receive inventory services, logging, configuration, and remote access
through dependency injection. They may not open SQLite directly or implement SSH;
all SSH behavior must use the single `SSHManager` supplied by LIM.
