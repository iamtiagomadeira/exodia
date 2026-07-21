"""Read-only ABAP readiness checks reached over RFC (Phase 1 — SAP MIG).

These map the "SAP MIG" pre-migration and post-processing *checks* of an ECS
cutover plan onto Exodia checks. Every check here is strictly read-only: it
opens an RFC connection, calls a read function module (or reads a table), and
returns a structured, timed Result with evidence — never mutating the system.
"""
