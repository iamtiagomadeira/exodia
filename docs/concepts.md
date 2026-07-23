# Core Concepts

The toolkit has a small, deliberate vocabulary. A handful of ideas explain the
whole tool.

## 1. Check — a read-only validation

A **check** answers one question about a live system and never mutates it:
*"is the source tenant online?"*, *"does the target have enough disk?"*,
*"do the HANA revisions match?"*. It runs, reads, and returns a structured
**result** with a status, a human summary, and labelled **facts**.

```
✅ PASS   ⚠️ WARN   ❌ FAIL   ⏭️ SKIP   💥 ERROR
```

Checks are safe to run anywhere, any time — nothing changes. Every check also
carries an intrinsic **severity** (Blocking / Advisory / Info) that decides how
a FAIL is treated at a phase gate — see [§6](#6-severity-the-gate-role-of-a-check).

## 2. Action — a guarded state change

An **action** changes something (creates a replica, stops app servers, applies
parameters). Every action runs the same safe-execution flow:

```
pre-checks → dry-run (default) → confirm → execute → verify → rollback (documented)
```

- **Dry-run is the default.** It shows the exact command(s) and touches nothing.
- **Execute requires explicit opt-in** (`--execute --yes`).
- Some actions add gates: a **customer-confirmation gate** (stopping the
  customer's app servers won't run until `customer_confirmed=true`), a
  **typed-name confirmation** (type the target tenant to proceed), or a
  **manual attestation** (the toolkit performs nothing; you record that you did an
  off-system step, e.g. emailed the customer).

Commands are always argument lists (`list[str]`) — **never** `shell=True`. HANA
auth goes through the secure store (`hdbsql -U <key>`), so no secret ever
reaches a command line or a log.

## 3. Runbook — an ordered sweep with one verdict

A **runbook** bundles a set of read-only checks into one ordered run and rolls
them up into a single aggregate **verdict**. It re-reads the live system every
time (no cached state), so it's idempotent and safe to re-run.

```bash
exodia runbook tenant-copy.hana.readiness-target --config target.yaml
```

Runbooks map to the **four cutover phases**, so the report groups results the
way a migration team reasons about the day:

| Phase | What happens | Downtime? |
|---|---|---|
| **Preparation** | read-only readiness + parity on source & target | no |
| **Ramp-Down** | quiesce the source (drain queues, lock users, stop servers) | starts |
| **Downtime** | the replica is created and synced | yes |
| **Post-Activities** | re-open + validate the target | ends |

## 4. Evidence — a sealed, tamper-evident audit trail

Every run — check, action, or runbook — writes an **evidence bundle**:

```
evidence/<methodology>/<SID>/<UTC-timestamp>/
    manifest.json   chain-of-custody + SHA-256 of every artifact
    run.jsonl       append-only event log
    results.json    the structured results
    report.md       human-readable report
```

- **Tamper-evident:** `exodia evidence verify <dir>` re-hashes every artifact
  and proves nothing was altered after the fact.
- **Shareable:** `exodia report --format html` produces a phase-grouped document
  with a colour-coded verdict banner; `--format csv` opens in Excel.
- **Searchable:** JSONL/JSON, not screenshots.

This replaces the manual "paste screenshots into a handover doc" step with an
audit trail generated as a by-product of doing the work.

## 5. Snapshot & Compare — the air-gapped model

In a real ECS/HEC engagement the source (customer) and target (HEC) sit in
**isolated networks** — one host rarely reaches both. The toolkit automates the
consultant's manual "read the source, log on to the target, compare against my
runbook" loop with two commands:

```bash
# in the customer network — capture a signed snapshot of one side:
exodia snapshot tenant-copy.hana.readiness-source --side source --config source.yaml -o source.json

# carry source.json across the air gap, then in the HEC network — diff it live:
exodia compare source.json --against tenant-copy.hana.readiness-target --side target --config target.yaml
```

A **snapshot** is a self-contained JSON file with every check's measured facts
and a **SHA-256 self-hash**. `compare` verifies that hash first (rejecting a file
altered in transit), then produces a check-by-check **source-vs-target diff**
with an aligned / diverge verdict. It carries no secrets — only measured facts.

## 6. Severity — the gate role of a check

Not every finding blocks a migration. Modelled on a real SAP Cutover Plan, every
check declares an **intrinsic severity** — its role at a phase gate:

| Severity | Icon | A FAIL means | Example |
|---|---|---|---|
| **Blocking** | 🔴 | the copy fails technically or risks data loss — a **NO-GO** | no recoverable backup, HSR not in SYNC, insufficient target space, active users at quiesce |
| **Advisory** | 🟡 | system hygiene / go-live quality — never blocks; documented for sign-off | ST22 short-dumps, SPAM queue, spool, transports, mount-point >80% |
| **Info** | ⚪ | context only — display-only, never a gate | recorded baselines, versions |

Severity is **intrinsic to the check**, but the **gate policy is
per-engagement**: a `gate:` block in the config can reclassify a check for a
given customer without touching code. This is the split behind the whole gate
engine — see **[Gates & the Exception Report](gates.md)**.

## 7. Gate verdict & the exception report

The **gate engine** rolls the graded results of a run up into a per-phase
**GO / NO-GO / GO-WITH-OVERRIDE / PENDING** decision, and into an exportable
**exception report** — the advisory + override artifact the customer signs off.
Surface them on any runbook with `--gate`, `--exceptions`, or `--export`:

```bash
exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml --exceptions
```

Only blocking findings can produce a NO-GO; advisories accumulate into the
report; a blocking NO-GO can be consciously **overridden** with a recorded
*who / what / when / why*. Full detail in **[Gates & the Exception Report](gates.md)**.

---

## How they fit together

```
   Checks  ──grouped into──▶  Runbooks  ──run──▶  Verdict + Evidence bundle
     │                                              │        │
     │                                              ▼        │
     │                                    Gate engine ──▶ GO / NO-GO
     │                                    (per phase)   + exception report
     │                                                           │
     └── Actions (guarded) ── each phase ───────────────────────┘
                                                                 │
   Snapshot (one side) ──carry──▶ Compare (other side) ──▶ diff + Evidence
```

Everything is **auto-discovered**: add a check or action class under
`exodia/modules/` and it appears in `exodia menu`, `exodia list`, and any
runbook that references it — no central registration. See
[Authoring a Module](authoring-a-module.md).
