"""ABAP-layer operations, reached over SAP NetWeaver RFC.

Where the ``system_copy`` modules speak to HANA (hdbsql), these speak to the
ABAP application server via RFC (pyrfc / the SAP NW RFC SDK). This is the layer
an ECS/HEC cutover plan calls "SAP MIG" work: the SM51/CVERS/SMQ/SMLG readiness
checks a Basis engineer would otherwise click through by hand, one transaction
at a time, on three systems.

Phase 1 scope: read-only *readiness* checks only. State-changing ABAP actions
(lock users, start/stop instances, switch profiles) are a later phase and live
under their own guarded Action subclasses when added.
"""
