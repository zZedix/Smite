# Changelog

All notable changes to the Smite project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- **UI-based Backup & Restore**: New Settings section for manual backup and restore
  - Download backup button with security warnings
  - Upload restore with metadata preview
  - Multi-step confirmation dialogs for safety
  - Pre-restore automatic safety backup
  - Support for server-to-server migration workflow
- **Panel UUID tracking**: Unique panel identifier stored in database
- **Backup manifest**: Each backup includes metadata (version, checksums, node/tunnel counts)

### Security
- Multi-panel safety warnings to prevent dual-panel operation
- Explicit checkbox confirmation required before restore
- Automatic pre-restore backup creation

---

## [0.8.0] - Previous Release

### Features
- Multiple tunnel types: GOST, Backhaul, Rathole, Chisel, FRP
- Iran and Foreign node management
- Telegram bot integration
- CLI tools for panel and node management
- HTTPS support with Let's Encrypt
- Multi-architecture support (amd64, arm64)
