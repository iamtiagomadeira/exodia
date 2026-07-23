# Gates & the Exception Report

Not every finding blocks a migration. A short-dump in ST22 doesn't fail a HANA
copy; a missing recoverable backup does. The toolkit encodes exactly that
distinction — modelled on a real SAP Cutover Plan — so it can tell you not just
*what* it found but **whether you may proceed**.

Two ideas do the work:

- **Severity** — a check's *intrinsic* gate role: Blocking, Advisory, or Info.
- **Gate policy** — a *per-engagement* overlay that can reclassify checks and
  control overrides, without touching code.

The engine turns a run into a per-phase **gate verdict** and an exportable
**exception report**.

---

## 1. Severity — the intrinsic gate role

Every check declares one of three severities:

| Severity | Icon | A FAIL means | Blocks a gate? |
|---|---|---|---|
| **Blocking** | 🔴 | the copy fails technically or risks data loss | **yes** — produces a NO-GO |
| **Advisory** | 🟡 | system hygiene / go-live quality; the copy succeeds anyway | no — feeds the exception report |
| **Info** | ⚪ | context only (recorded baselines, versions) | no — display-only |

The rule, taken straight from the real Cutover Plan:

> **Block only what makes the copy fail technically or lose data. Everything
> that is system hygiene / go-live quality is advisory — captured as a
> documented exception the customer acknowledges.**

### What's blocking vs advisory today

| Blocking (🔴) — a NO-GO if it fails | Advisory (🟡) — documented, never blocks |
|---|---|
| No recoverable backup < 24h | ST22 short-dumps |
| HSR secondary not in SYNC before takeover | SPAM support-package queue |
| Insufficient target data / log space | Spool requests (SP01) |
| Missing / invalid migration key | Pending transports (STMS/SE01) |
| Active users / locks / pending updates at quiesce (SM12/SM13/SMQ) | Mount-point >80% |
| Target tenant name already in use | SGEN recompilation state |
| Post-copy table / catalog consistency (target) | STMS reconfigure, non-compliant RZ10 parameters |

The ABAP **ramp-down quiesce** checks (active users, locks, queued jobs) are
**blocking** — writing during a quiesce corrupts the export. The ABAP
**hygiene** checks (ST22 short-dumps, SPAM, spool, transports) are **advisory**
by default, and reclassifiable per engagement.

> Severity lives on the check class (`severity = Severity.ADVISORY`). Checks that
> predate this model — those that only declared `blocking: bool` — keep working
> unchanged: `blocking=True` maps to Blocking, otherwise Advisory.

---

## 2. The gate verdict

At a phase gate the engine grades every result and returns one decision:

| Decision | Icon | When |
|---|---|---|
| **GO** | ✅ | nothing blocking is open — safe to advance |
| **NO-GO** | 🔴 | at least one blocking finding is open and not overridden |
| **GO-WITH-OVERRIDE** | ⚠️ | blocking finding(s) existed but every one was consciously overridden |
| **PENDING** | ⏳ | nothing was graded yet (all skipped) — not decided |

Gates are **discrete decision points, not one gate per phase**. The real plan
has two hard technical gates — *entry-to-downtime* and *technical-GO* — plus a
joint customer + SAP business GO/NO-GO after functional testing. The per-phase
panel is the *instrument*; the gate is the *decision*. The business GO/NO-GO is
outside the tool's authority: the toolkit *informs* it with the evidence pack,
it does not make it.

### Seeing the verdict — the runbook flags

Three layered flags on `exodia runbook` surface the gate engine. Each implies
the previous one:

```bash
# 1. Just the per-phase gate verdicts, after the results table:
exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml --gate

# 2. The gate table PLUS the full exception & advisory report (implies --gate):
exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml --exceptions

# 3. Also write that report as portable Markdown (implies --exceptions):
exodia runbook tenant-copy.hana.readiness --config tenant-copy.yaml --export exceptions.md
```

`--gate` renders a table like:

```
                         Gate Verdicts (per phase)
  ✅  Preparation Phase           GO                12/12  GO — 12/12 passed, no blockers
  🟡  Post-Activities Phase       GO                 3/4   GO — advisories noted
```

Without any flag, the runbook output is unchanged — the gate machinery is
strictly opt-in. In the **TUI** (`exodia tui`), the phase board shows the same
verdict as a badge per phase — `GO`, `NO-GO`, or `GO*` (go-with-override) —
computed by the same engine.

---

## 3. The exception report

`--exceptions` prints (and `--export` writes) the artifact that circulates with
the customer for sign-off. It mirrors the real Cutover Plan's *exception
template*: nothing is ever silently ignored — every deviation leaves a trail.

Its structure:

1. **Header** — system, method, and generation timestamp.
2. **Gate summary** — each phase's GO / NO-GO / PENDING with the passed tally.
3. **Blocking issues open** — any 🔴 still open (empty when the gate can pass).
4. **Advisories** — every 🟡 finding, with its side and owner, for the customer
   to decide *clean vs ignore*.
5. **Override audit trail** — every conscious decision to proceed past a
   blocker: who, what, when, why.

The Markdown export is a self-contained document you attach to a handover email
or ticket. It carries no secrets — only findings and dispositions.

---

## 4. The gate policy — per-engagement, in config

Severity is intrinsic, but two customers can want different treatment of the
same finding. A `gate:` block in the engagement config reclassifies checks and
controls overrides — **the check code never changes**:

```yaml
# tenant-copy.yaml
db_type: hana
source: PRD
target: QAS
params:
  target_userstore_key: TGTSYS
gate:
  # Reclassify specific checks for THIS customer (name: severity).
  reclassify:
    abap.readiness.short-dumps: blocking   # a compliance-strict customer wants ST22 dumps to block
  # Supervised mode: a junior executing a senior's plan may NOT override —
  # they must escalate instead of proceeding. Default: false (expert mode).
  forbid_override: false
  # Optional allow-list: only these checks may be overridden, even in expert
  # mode. Empty = any blocking check may be overridden (with an audit entry).
  overridable: []
```

- **`reclassify`** — map a check name to `blocking`, `advisory`, or `info`.
- **`forbid_override`** — when `true`, no blocking finding can be overridden
  (supervised mode).
- **`overridable`** — restrict overrides to a named allow-list.

An empty or absent `gate:` block reproduces the intrinsic severities and permits
overrides — the safe default for a senior-operated tool.

---

## 5. Overrides — a conscious, audited decision

A blocking NO-GO can be **overridden** so the operator is never *trapped* by the
tool at 3am. But an override is a first-class, recorded decision — it captures:

| Field | Meaning |
|---|---|
| **check** | the finding being overridden |
| **who** | operator identity / initials |
| **when** | UTC timestamp |
| **why** | the conscious justification (required, non-empty) |

Every override lands in the exception report's audit trail and in the sealed
evidence bundle. That log **is** the handover exception template the operator
has to produce anyway — so the tool saves work instead of adding ceremony. The
verdict becomes **GO-WITH-OVERRIDE**, distinct from a clean GO, so the record is
honest.

The design intent, in one line:

> **The tool is a co-pilot, not a lock.** It renders a verdict and refuses to
> auto-advance on a blocking-open, but the operator can acknowledge and proceed —
> with every override recorded.

---

## See also

- **[Core Concepts](concepts.md)** — checks, actions, runbooks, evidence.
- **[HANA Tenant Copy](tenant-copy.md)** — the operator guide, end to end.
- **[SAP MIG Cutover](cutover.md)** — the four-phase ABAP playbook.

> The gate model is derived from anonymized real SAP migration cutover
> artifacts. The design reference (`COP_model.md`, in the repo root) documents
> the mapping in full, with **no** customer, SID, host, person, or schema
> identifiers.
