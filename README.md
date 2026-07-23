# SAP Migration Toolkit

<p>
  <a href="https://github.com/iamtiagomadeira/sap-migration-toolkit/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/iamtiagomadeira/sap-migration-toolkit/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="https://github.com/iamtiagomadeira/sap-migration-toolkit/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/iamtiagomadeira/sap-migration-toolkit/actions/workflows/codeql.yml/badge.svg" /></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue" />
  <img alt="Tests" src="https://img.shields.io/badge/tests-697%20passing-brightgreen" />
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green" /></a>
  <a href="https://iamtiagomadeira.github.io/sap-migration-toolkit/"><img alt="Docs" src="https://img.shields.io/badge/docs-online-blue" /></a>
</p>

> _Codename: Exodia_ — a stateless, plugable command-line **cockpit** (CLI + TUI)
> that automates the repetitive, error-prone parts of **SAP system migrations**:
> HANA tenant copy, backup/restore, HANA System Replication, and the ABAP
> cutover (ramp-down → downtime → post-activities).

Think **`ansible --check` meets a SAP Basis runbook**: the toolkit validates
prerequisites, then executes migration steps with dry-run, explicit
confirmation, verification, documented rollback — and a sealed, tamper-evident
audit trail for every run. It runs on any Linux box, needs no database of its
own, and never phones home.

On top of that it grades each run against a **severity gate engine** modelled on
a real SAP Cutover Plan: it renders a per-phase **GO / NO-GO** verdict and an
exportable **exception report** — the artifact the customer signs off — so the
tool tells you not just *what* it found but *whether you may proceed*.

📖 **[Read the docs →](https://iamtiagomadeira.github.io/sap-migration-toolkit/)**

---

## Why

An SAP system copy today is largely manual: a consultant babysits `sapinst`
screens for hours, runs prerequisite checks by hand across a dozen transactions
and two SYSTEMDBs, judges which findings actually block the cutover, and pastes
screenshots into a handover document. It is slow, inconsistent, and hard to
audit.

The toolkit turns that runbook into **repeatable, monitored, auditable
automation**, while keeping the human in control for the decisions that matter.
It models a real ECS/HEC cutover as first-class objects: read-only **checks**,
guarded **actions**, ordered **runbooks**, a severity **gate engine**, and
sealed **evidence**.

## What's inside

| | |
|---|---|
| **107 checks** | read-only validations across HANA, ABAP (RFC), OS-level and landscape config |
| **38 actions** | guarded state changes: replica trigger, ramp-down, post-activities, HSR config |
| **7 runbooks** | ordered read-only sweeps with a single aggregate verdict |
| **4 cutover phases** | Preparation → Ramp-Down → Downtime → Post-Activities |
| **3 severity roles** | Blocking · Advisory · Info — the gate engine's grading model |

_(Live counts on your install: run `exodia doctor`.)_

## Principles

- **Stateless** — runs and exits; no memory, no embedded planning database.
- **Two categories, one safety model:**
  - **Checks** are read-only. Safe to run anywhere, any time.
  - **Actions** change state. Guarded: pre-checks → dry-run (default) → explicit
    confirmation → execute → verify → documented rollback.
- **Gate at the boundaries, advise in between.** A check carries an intrinsic
  **severity**; the gate engine turns a run into a per-phase GO / NO-GO verdict.
  Only *blocking* findings can produce a NO-GO — hygiene findings become
  documented advisories the customer acknowledges.
- **Safe by construction** — commands are argument lists, never `shell=True`;
  secrets are never logged or placed on a command line (HANA auth via the secure
  user store, `hdbsql -U <key>`); SSH uses host-key verification.
- **Plugable** — drop a module under `exodia/modules/` and it is auto-discovered
  in the menu, `list`, and runbooks. No central wiring.
- **Evidence by default** — every run seals a bundle with a SHA-256 manifest,
  an append-only event log, and a phased HTML/CSV report.
- **Defaults + escape hatch** — opinionated defaults for the 80% path, plus
  config/hooks for the 20% (including a per-engagement gate policy).

## Install

```bash
# from source (Python 3.11+):
git clone https://github.com/iamtiagomadeira/sap-migration-toolkit.git
cd sap-migration-toolkit
python3 -m venv .venv && .venv/bin/pip install -e .
```

## Quickstart

The easiest way in is the **interactive wizard** — no long commands, no YAML to
hand-craft. It discovers hdbuserstore keys for you and asks only the fields each
operation needs:

```bash
exodia menu        # guided, operator-friendly front door
```

Or drive the whole toolkit from a **full-screen grid cockpit** — a
keyboard-driven Textual UI with the operations tree, a live log, a streaming
results table and a phase board that shows a GO / NO-GO gate badge per phase:

```bash
pip install -e '.[tui]'   # one-time: pull in the optional TUI extra
exodia tui                # launch the cockpit
```

![SAP Migration Toolkit — TUI cockpit](docs/assets/tui-cockpit.png)

Read-only **checks** and **runbooks** run for real from the TUI and stream in;
state-changing **actions** stay on the guarded `exodia run … --execute` flow.
Navigate with arrows / `hjkl` / `Tab`, `Enter` to run, `f` to zoom a panel,
`q` to quit.

Prefer direct commands? Everything is scriptable by name:

```bash
exodia list                 # every discovered check & action
exodia runbooks             # every readiness sweep
exodia cutover-plan         # the day-of playbook: 4 phases, exact commands, gates
exodia doctor               # self-check

# a read-only readiness sweep (safe, re-runnable):
exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml

# same sweep, with the per-phase gate verdict and the exception report:
exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml \
    --exceptions --export exceptions.md
```

Dry-run is the **default** for actions — pass `--execute --yes` to run for real.

### Exit codes

Every command exits with an automation-friendly code:

| Code | Meaning |
|---|---|
| `0` | success — nothing blocking (or a `compare` where sides align) |
| `1` | a blocking failure — a FAIL/ERROR result, or diverging sides in `compare` |
| `2` | usage error — unknown operation/runbook, or invalid config/option |

## Key concepts

| Concept | What it is |
|---|---|
| **Check** | a read-only validation returning one structured `Result` (PASS / WARN / FAIL / SKIP / ERROR) |
| **Action** | a guarded state change: pre-checks → dry-run (default) → confirm → execute → verify → rollback |
| **Runbook** | an ordered, read-only sweep of checks with one aggregate verdict and a sealed evidence bundle |
| **Severity** | a check's intrinsic gate role — **Blocking** / **Advisory** / **Info** |
| **Gate verdict** | a per-phase **GO / NO-GO / GO-WITH-OVERRIDE / PENDING** decision computed by the gate engine |
| **Exception report** | the exportable advisory + override artifact the customer signs off (terminal + Markdown) |
| **Evidence bundle** | a sealed, tamper-evident record (SHA-256 manifest, event log, results, report) per run |

See **[Core Concepts](https://iamtiagomadeira.github.io/sap-migration-toolkit/concepts/)**
and **[Gates & the Exception Report](https://iamtiagomadeira.github.io/sap-migration-toolkit/gates/)**
for the full model.

## The tenant-copy cutover, end to end

The toolkit maps a HANA tenant copy onto the four cutover phases:

1. **Preparation** — readiness checks on both sides (versions, ports, params,
   space, connectivity) while the source stays live.
2. **Ramp-Down** — quiesce the source (users out, jobs stopped, locks clear)
   and take the final recoverable backup.
3. **Downtime** — the copy/restore or HANA System Replication takeover itself,
   behind the entry gate where every blocking finding must be clear.
4. **Post-Activities** — reconfigure, reconcile, and validate the target, then
   the joint customer + SAP GO/NO-GO.

**Air-gapped by design.** Source (customer) and target (HEC) usually sit in
isolated networks. Capture one side into a **signed, tamper-evident snapshot**,
carry it across, and diff it against the other — the consultant's manual "read
here, compare there" loop, automated:

```bash
# in the customer network — capture a signed snapshot:
exodia snapshot tenant-copy.hana.readiness-source --side source --config source.yaml -o source.json

# carry source.json across, then in the HEC network — diff it live:
exodia compare source.json --against tenant-copy.hana.readiness-target --side target --config target.yaml
```

See the **[full coverage map](https://iamtiagomadeira.github.io/sap-migration-toolkit/tenant-copy-coverage/)**
for every check and action, phase by phase.

## Gates & the exception report

Not every finding blocks a migration. Modelled on a real SAP Cutover Plan, each
check declares an intrinsic **severity**:

- **Blocking** — makes the copy fail technically or risks data loss (no
  recoverable backup, HSR not in SYNC before takeover, insufficient target
  space, active users during quiesce). A blocking FAIL is a **NO-GO** for the
  phase gate.
- **Advisory** — system hygiene / go-live quality that does *not* fail the copy
  (ST22 short-dumps, SPAM queue, spool, transports, mount-point >80%). Never
  blocks — it feeds the exportable exception report the customer signs off.
- **Info** — context only (recorded baselines, versions). Display-only.

The `runbook` command surfaces this with three layered flags:

```bash
exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml --gate
#   → a per-phase table of GO / NO-GO / GO-WITH-OVERRIDE verdicts

exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml --exceptions
#   → the gate table + the full advisory report (implies --gate)

exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml --export exceptions.md
#   → also writes that report as portable Markdown (implies --exceptions)
```

Severity is **intrinsic** (in the check), but the **gate policy is
per-engagement**: a `gate:` block in the config can reclassify a check for a
given customer (e.g. treat ST22 dumps as blocking), forbid overrides
(supervised mode), or restrict which checks may be overridden. A blocking NO-GO
can be **overridden** so the operator is never trapped at 3am — but every
override records *who / what / when / why* and lands in the exception report as
the handover sign-off trail.

## Example: a guarded replica trigger

```bash
# 1. Readiness (read-only) — safe any time, changes nothing
exodia runbook tenant-copy.hana.readiness-target --config target.yaml
#   → phase-grouped table + one verdict; exit 1 if any blocker

# 2. Preview the copy (dry-run is the default — nothing runs)
exodia run tenant-copy.hana.copy-tenant --config target.yaml
#   [DRY-RUN] would run: CREATE DATABASE QAS AS REPLICA OF PRD AT '<src>:3<nn>13'

# 3. Execute for real — explicit opt-in, typed-name confirmation, live dashboard
exodia run tenant-copy.hana.copy-tenant --config target.yaml --execute --yes --monitor
#   → pre-checks → execute (progress bar + log tail) → verify → rollback on failure
```

## Supported scenarios

| Methodology | Databases | Coverage |
|---|---|---|
| **Tenant Copy** | HANA | cross-host (customer → HEC), replication or backup method, HSR/SSL config, mock-run isolation, post-copy consistency — **most complete** |
| **ABAP cutover (SAP MIG)** | any | pre-migration checks, ramp-down + post-activities actions, OS & landscape validation |
| Backup / Restore | HANA, SAP ASE | native tools + SWPM system copy |
| HANA System Replication | HANA | create / finalize / enable replica |
| Java (AS Java) system copy | HANA | SLD, SECSTORE, RFC, UME post-copy |

## Security & privacy

- No secrets on command lines or in logs — HANA auth goes through the secure
  user store (`hdbsql -U <key>`); SSH is key-based with host-key verification.
- Commands are always argument lists (`list[str]`) — **never** `shell=True`.
- Report security issues privately via
  [GitHub Security Advisories](https://github.com/iamtiagomadeira/sap-migration-toolkit/security/advisories/new)
  — see [SECURITY.md](SECURITY.md).

## Documentation

- **[Getting started & concepts](https://iamtiagomadeira.github.io/sap-migration-toolkit/)**
- **[Core Concepts](https://iamtiagomadeira.github.io/sap-migration-toolkit/concepts/)**
- **[Gates & the Exception Report](https://iamtiagomadeira.github.io/sap-migration-toolkit/gates/)**
- **[HANA Tenant Copy — operator guide](https://iamtiagomadeira.github.io/sap-migration-toolkit/tenant-copy/)**
- **[Tenant Copy — full coverage](https://iamtiagomadeira.github.io/sap-migration-toolkit/tenant-copy-coverage/)**
- **[SAP MIG cutover](https://iamtiagomadeira.github.io/sap-migration-toolkit/cutover/)**
- **[Authoring a module](https://iamtiagomadeira.github.io/sap-migration-toolkit/authoring-a-module/)**

## Contributing

Contributions are welcome — new methodology modules, checks, and SAP Note
mappings especially. See [CONTRIBUTING.md](CONTRIBUTING.md).

> **Note on SAP Notes:** the toolkit references SAP Note *numbers* for
> remediation and never reproduces their copyrighted text. SAP, HANA, and
> related marks are trademarks of SAP SE; this is an independent, unofficial
> open-source project.

## License

MIT © Tiago Madeira

## Star History

<a href="https://www.star-history.com/#iamtiagomadeira/sap-migration-toolkit&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=iamtiagomadeira/sap-migration-toolkit&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=iamtiagomadeira/sap-migration-toolkit&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=iamtiagomadeira/sap-migration-toolkit&type=Date" />
 </picture>
</a>
