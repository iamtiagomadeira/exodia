# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `exodia report [BUNDLE]` renders an evidence bundle as a standalone,
  shareable HTML document plus its Markdown summary; defaults to the most
  recent bundle and writes outside the sealed directory so the tamper-evident
  manifest stays intact.
- Evidence-by-default: every run writes a self-contained, tamper-evident audit
  bundle (`manifest.json` with per-artifact SHA-256, append-only `run.jsonl`,
  `results.json`, `report.md`, harvested `artifacts/`). `exodia evidence verify`
  re-hashes a bundle to prove it was not altered; `exodia evidence attach` adds
  external logs and re-seals.
- System Copy methods **export/import** (SWPM — R3load for ABAP, JLoad for
  Java), **HANA System Replication (HSR)**, and a standalone **Solution
  Manager** post-copy module, each with real read-only pre-checks.
- Interactive `exodia menu`: pick family → method + stack → operation, with a
  stack-compatibility gate that blocks unsupported combinations (e.g. Java +
  backup/restore, which SAP does not support).
- `exodia doctor` self-check and a "Run ALL" option with a clear go/no-go
  verdict.
- CI test-coverage floor of 75% and a `.pre-commit-config.yaml` mirroring the
  CI gates (ruff, ruff-format, mypy).
- Automated PyPI release workflow via Trusted Publishing (OIDC), triggered by
  `v*` tags.

## [0.1.0] - 2026-07-20

### Added

- Initial public release of the SAP Migration Toolkit (codename Exodia): a
  stateless executor for SAP migration operations with auto-discovered
  methodology modules, guarded actions (dry-run → confirm → execute → verify →
  rollback), a YAML-backed error/remediation knowledge base, and pre-checks for
  HANA/ASE backup-restore and HANA tenant copy.

[Unreleased]: https://github.com/iamtiagomadeira/sap-migration-toolkit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/iamtiagomadeira/sap-migration-toolkit/releases/tag/v0.1.0
