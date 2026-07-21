"""ABAP ramp-down — guarded state-changing steps to quiesce the source.

The cutover-plan ramp-down rows as guarded Actions: suspend the background
scheduler (BTCTRNS1), adapt operation modes (SM63), stop all application servers
(sapcontrol — gated behind explicit customer confirmation), and the manual
"inform customer that ramp-down is complete" attestation. Read-only ramp-down
verification (SM12/SM13/SMQ/SM37/ICNV/...) lives under ``readiness``.
"""
