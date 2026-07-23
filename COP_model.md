# Cutover Plan (COP) — Reference Model for the Gate Engine

> **Provenance & privacy.** This document abstracts the *structure and semantics*
> of real SAP migration cutover artifacts (a Cutover Plan spreadsheet template, a
> technical execution runbook, and an ECS post-migration handover checklist) into
> a generic, vendor-neutral model. **No customer names, SIDs, hostnames, IPs,
> people, URLs, or schema names are reproduced here** — only *what* is verified at
> each step, *how* it is classified, and *how* it is reported. It exists to drive
> the design of the toolkit's severity-gate engine, not to leak any engagement.

---

## 0. Executive summary — what the real COP teaches us

Three findings reshape the gate design:

1. **The real COP does NOT block on hygiene checks.** ST22 dumps, SE80 inactive
   objects, SE14 temp tables, SPAM queue, mount-point >80% — none of these stop a
   cutover. They are managed as **documented exceptions** in an *exception
   template* that circulates to the customer / ECS for **acknowledgement**. This
   is exactly the "exportable advisory report" model — and the real process
   confirms the intuition: *the copy succeeds regardless; the customer decides
   clean-vs-ignore and signs off.*

2. **Severity is decoupled from policy — via two extra axes the COP already uses.**
   Every step carries a **SIDE** (source / target / both / org) and a
   **RESPONSIBLE** owner (customer / migration team / ECS / CMA). Classification is
   not one-dimensional: the same finding routes differently depending on who owns
   it and which side it lives on. This validates a severity-in-the-check /
   policy-in-the-config split.

3. **Gates are discrete go/no-go points, NOT one gate per phase.** The real plan
   has exactly **two hard technical gates** (entry-to-downtime and technical-GO)
   plus **one joint customer+SAP go/no-go** after functional testing. A gate is an
   *event with named approvers and explicit criteria*, not a per-phase traffic
   light. The per-phase panel is the instrument; the gate is the decision.

---

## 1. Phase model (as the real COP names them)

The COP spreadsheet numbers phases in Roman numerals. Mapping to the toolkit's
four internal phases:

| COP phase (real naming) | Toolkit phase | Notes |
|---|---|---|
| **I — Preparation** | PREPARATION | Largest phase; source + target checks, profile generation, credential validation. Runs *before* the downtime window. |
| **II — Migration: Ramp-down** | RAMP_DOWN | Stop 3rd-party connections, stop additional app servers, user lock (with exception list), handover to migration team. |
| **III — Migration: Technical downtime** | DOWNTIME | Stop app servers → export → transfer → import → stop source. The expensive, contracted window. |
| **IV — Post-processing (migration team)** | POST | Parameter adjustment, profile switch, start, technical checks, audit doc. |
| **V — Post-processing (ECS/provider)** | POST | Handover to provider post-processing. |
| **VI — Post-processing (customer)** | POST | Customer verifies SMLG/RZ10/STMS/SPAD, SSO, certificates. |
| **VII — Functional testing** | POST | Customer test + validation → **joint GO/NO-GO decision**. |
| **VIII — System ramp-up** | POST | Unlock users, unlock batch jobs (BTCTRNS2), enable queues, announce live. |
| **IX — Post-GoLive activities** | POST | Follow-up / lessons learned. |

**Design takeaway:** the toolkit's 4-phase model is correct, but POST is a
*compound* of five real COP phases with different owners (migration team → ECS →
customer). The phase panel should be able to show ownership, not just status.

---

## 2. COP step-list structure (anonymized)

Each row in the real Cutover Plan carries these fields — this is the shape the
toolkit's advisory/report rows should mirror:

| Field | Meaning | Toolkit mapping |
|---|---|---|
| **No** | Step ID (e.g. 1001, 3004) — grouped by phase (1xxx=Prep, 3xxx=Downtime, 5xxx=ECS PP, 6xxx=Customer PP, 8xxx=Ramp-up) | check/action id |
| **SIDE** | `source` / `target` / `both` / `org` | new axis — where the check runs |
| **Item Description** | What is done | check title |
| **Status** | Open / In Process / Completed / Not Ok / NA / Obsolete / Released / Imported / Not required | result state |
| **Responsible** | customer / migration-team / ECS / CMA / ALL | owner axis |
| **Owner** | Named individual | (out of scope — PII) |
| **Date / Duration** | Planned + actual | timing |
| **Customer Comments** | Customer-side notes | advisory note field |
| **SAP Comments** | Provider-side notes | advisory note field |

The **Status vocabulary** (from the Helper sheet) is richer than pass/fail and
maps cleanly to gate roles:

- `Completed` / `Released` / `Imported` → **PASS**
- `Not Ok` → **FAIL** (candidate blocking)
- `NA` / `Not required` / `Obsolete` → **INFO / skipped**
- `Open` / `In Process` → **PENDING**

---

## 3. Representative steps per phase, with SIDE, transaction, method, and role

> Method codes: **BR**=Backup/Restore · **EI**=Export/Import · **TC**=Tenant Copy ·
> **HSR**=HANA System Replication · **ALL**=method-independent.
> Role: 🔴 **BLOCK** · 🟡 **ADVISORY** · ⚪ **INFO**.

### I — Preparation

| Step (generic) | SIDE | Txn/Cmd | Method | Role | Rationale (Basis) |
|---|---|---|---|---|---|
| Validate all master passwords (ABAP DDIC 000, Java admin, HANA SYSTEM SystemDB+TenantDB, schema user, OS <sid>adm, root/sudo) | both | — | ALL | 🔴 | *"A credential discovered missing during downtime is a hard blocker."* The single most cited failure cause. Validate before the window, never inside it. |
| Recent recoverable data backup < 24h + log backups successful | source | `M_BACKUP_CATALOG` | BR/EI | 🔴 | No recoverable backup = no rollback target = data-loss risk. Hard gate. |
| Target migration storage has sufficient free space | target | `df -h` | ALL | 🔴 | Insufficient target space aborts import mid-flight. |
| SecStore key-phrase / algorithm alignment (Java) | both | `checkKeyPhrase.sh`, `SecStore.key` | BR/EI | 🔴 | *"#1 cause of failure in Java system copies."* Algorithm version + key phrase must match source↔target. |
| Migration key present/valid (heterogeneous copy) | source | R3load/SWPM | EI | 🔴 | Absent key → export refuses to run. |
| log_mode correct for method (HSR/Tenant Copy → `normal`) | source | `global.ini` log_mode | HSR/TC | 🔴 | Wrong log mode breaks replication/recovery chain. Check replication status *before* changing. |
| Architecture / byte-order match (endianness, scale-up vs scale-out) | both | `lscpu`, `GetSystemInstanceList` | ALL | 🔴 | Endianness mismatch = incompatible copy. Scale-out correction needs downtime — flag to PL early. |
| Timezone consistency across all DB+app nodes (incl. HA/DR) | both | `timedatectl`, HANA `CURRENT_UTCTIMESTAMP` | ALL | 🟡→🔴 | Mismatch causes data/scheduling inconsistency. Advisory if cosmetic, blocking if it affects data. Config-driven. |
| DB encryption status matches expected (DED/ATLAS) | both | `M_ENCRYPTION_OVERVIEW` | ALL | 🟡 | No downtime for the check. Mismatch → online encryption request during post-processing, not a copy blocker. |
| Compare DB (source vs target) | target | — | ALL | 🟡 | Planning input; drives sizing. |
| Record server nodes / JVM version / component levels | source | NWA System Information | ALL | ⚪ | Reference baseline for post-copy comparison. |
| Backup critical files (SecStore, profiles, instance dirs) | source | `cp -p` + `md5sum` | BR/EI | 🔴 | Corrupted SecStore is unrecoverable without this. Verify with checksum — *never trust without confirming*. |
| Third-party add-ons / DB client dependencies check | source | — | ALL | 🟡 | May need client install on target; advisory unless it blocks connectivity. |

### II — Ramp-down

| Step (generic) | SIDE | Txn/Cmd | Method | Role | Rationale |
|---|---|---|---|---|---|
| Stop/verify connections to 3rd-party systems | source | SM59 | ALL | 🟡 | Operational hygiene; not a copy blocker but a data-integrity risk if left live. |
| Stop additional application servers | source | — | ALL | 🔴 | Open app servers during export = inconsistent export. |
| Lock users (honor exception list: OS/DB/admin users) | source | SU10 / exception list | ALL | 🔴 | Active user sessions writing during quiesce corrupt the export. Exception list is mandatory. |
| Quiesce verify — no pending updates / locks / queued docs | source | SM13, SM12, SMQ1/2 | ALL | 🟡 | **Boundary case** — see §5. Pending updates *can* mean in-flight data, but the copy is at DB level. Advisory-with-verify: surface it, let the operator decide. |
| Confirm ramp-down complete + handover to migration team | source | — | ALL | 🔴 (gate) | **This is GO/NO-GO gate #1.** See §4. |

### III — Technical downtime

| Step (generic) | SIDE | Txn/Cmd | Method | Role | Rationale |
|---|---|---|---|---|---|
| Stop ABAP/Java application servers | source | — | ALL | 🔴 | Prereq for consistent export. |
| SWPM export (unattended) | source | SWPM/sapinst, R3load/JLOAD | EI | 🔴 | Core operation. Monitor export log. |
| Transfer export to target (with checksum) | both | rsync/lftp + md5 | EI | 🔴 | Corrupt/incomplete transfer = failed import. Verify checksum. |
| HANA data backup / restore to target tenant | both | HANA backup+recovery | BR | 🔴 | Core data movement. |
| HSR: secondary in SYNC before takeover | both | `hdbnsutil -sr_state` | HSR | 🔴 | Takeover with non-SYNC replication = data loss (RPO≠0). Absolute hard gate. |
| SWPM import (unattended) | target | SWPM/sapinst | EI | 🔴 | Core operation. Monitor import/migration-monitor log. |
| Stop on-prem source + source DB | source | — | ALL | 🔴 | Freeze point; source becomes rollback target until technical GO. |

### IV–VI — Post-processing (migration team → ECS → customer)

| Step (generic) | SIDE | Txn/Cmd | Method | Role | Rationale |
|---|---|---|---|---|---|
| Adjust instance-specific parameters, switch to generated profiles | target | RZ10, configtool | ALL | 🔴 | System won't start correctly otherwise. |
| Start application servers, verify all nodes green | target | NWA / SMLG | ALL | 🔴 | Startup failure = no go-live. |
| BDLS (logical system conversion) | target | BDLS | ALL | 🔴 | Wrong logical system names break integration/data references. |
| STMS reconfigure + consistency | target | STMS | ALL | 🟡 | Advisory — single-domain/standalone acceptable; broken TMS doesn't fail the copy but needs handover note. |
| SGEN recompilation | target | SGEN | ALL | 🟡 | Performance/first-use; not a copy blocker. Advisory. |
| Restore log_mode to standard (if changed) | target | global.ini | ALL | 🔴 | Leaving overwrite mode risks recovery chain post-go-live. Gate-2 criterion. |
| Check DB log mode / SSL / SSO / certificates | target | STRUST etc. | ALL | 🟡 | Security hygiene; advisory with exception documentation. |
| Java logs free of blocking errors | target | NWA logs | ALL | 🔴 | Blocking errors = NO-GO. Gate-2 criterion. |
| HANA tenant online + baseline backup taken | target | HANA | ALL | 🔴 | Gate-2 criterion; no post-copy backup = no protection. |
| Customer verifies SMLG/RZ12/RZ03/SM59/SM50/SM51/SM58/DBACOCKPIT/SLICENSE | target | (many) | ALL | 🟡 | Customer-side validation; advisory to the migration tool. |

### VII–IX — Testing, GO/NO-GO, ramp-up

| Step (generic) | SIDE | Txn/Cmd | Method | Role | Rationale |
|---|---|---|---|---|---|
| Functional test / validation | target | — | ALL | 🟡 | Customer-owned; toolkit records, doesn't gate. |
| **GO / NO-GO decision (joint customer + SAP)** | target | — | ALL | 🔴 (gate) | **Joint gate.** See §4. |
| Unlock users | target | SU10 | ALL | ⚪ | Ramp-up action. |
| Unlock batch jobs (BTCTRNS2), enable queues | target | SE38/SM37 | ALL | ⚪ | Ramp-up action. |
| Data-consistency: total row count source vs target | both | `M_CS_TABLES` SUM | ALL | 🔴 | *"An unexplained delta is a NO-GO."* Only explained deltas (temp/technical tables) pass. |
| Inactive/unloaded tables check | target | `M_CS_TABLES WHERE LOADED<>'FULL'` | ALL | 🟡 | Load-on-demand is normal for column store; advisory unless consistency check fails. |
| Table consistency check | target | `CHECK_TABLE_CONSISTENCY` | ALL | 🟡→🔴 | Long-running; genuine inconsistency is blocking, schedule off-peak. |

---

## 4. Gates — the real go/no-go points

The real process has **three discrete decision gates**, not a per-phase gate:

### 🚦 Gate #1 — Entry to downtime (end of ramp-down)
- **When:** ramp-down complete, before stopping app servers for export.
- **Approver:** migration team (technical), after customer confirms ramp-down.
- **Criteria (all must be green):** every credential validated; recoverable
  backup < 24h; target storage sufficient; key-phrase/architecture/log-mode
  aligned; users locked (exception list honored); no in-flight updates unaccounted.
- **Toolkit meaning:** this is where the 🔴 blocking checks *must* be resolved.
  Everything blocking is front-loaded here — nothing critical should first fire
  inside the paid window. **A critical gate that trips mid-downtime is a design
  defect.**

### 🚦 Gate #2 — Technical GO (end of post-processing)
- **When:** post-processing complete, before handover/ramp-up.
- **Approver:** migration team provides the *technical* GO; source system remains
  the rollback target until GO is confirmed.
- **Criteria:** all server nodes green; logs free of blocking errors; tenant
  online + baseline backup taken; log_mode restored; handover audit trail
  delivered; **row-count delta explained**.
- **Toolkit meaning:** the second hard gate. Everything here is verifiable —
  perfect for automated GO/NO-GO verdict.

### 🚦 Gate #3 — Business GO/NO-GO (joint, after functional testing)
- **When:** after customer functional testing.
- **Approver:** **joint customer + SAP** decision.
- **Toolkit meaning:** out of the tool's authority — the toolkit *informs* this
  decision with its evidence pack, it does not make it.

**Between gates there is no blocking** — only advisories that accumulate into the
exception report. This is the core insight: **gate at the boundaries, advise in
between.**

---

## 5. Boundary cases — the exact criterion the toolkit must encode

The real ECS handover checklist lists these as **exceptions to document**, not
blockers. This settles the classification debate:

| Check | Txn | Real-world handling | Toolkit role | Why |
|---|---|---|---|---|
| Runtime dumps | ST22 | "No *relevant* runtime errors" — relevance is judged, dumps are recorded as exception | 🟡 ADVISORY | Dumps live in runtime tables; the copy is at DB/tenant level. Migration completes regardless. |
| Inactive repository objects | SE80 | "Inactive objects cleared" is a handover *item*, managed via exception if not | 🟡 ADVISORY | Inactive objects travel as-is to target; they don't fail the copy. Go-live quality, customer's call. |
| Temp table entries | SE14 | Documented if present | 🟡 ADVISORY | Cleanup hygiene. |
| Update queue | SM13 | Verified during ramp-down | 🟡 ADVISORY-with-verify | Surface it; operator decides. In-flight data is real but DB-level copy captures committed state. |
| Lock entries | SM12 | Verified during ramp-down | 🟡 ADVISORY-with-verify | Same reasoning. |
| SPAM queue | SPAM | "SPAM queue clear" — handover item | 🟡 ADVISORY | Support-package state; not a copy blocker. |
| System log | SM21 | "No critical unresolved" — judged | 🟡 ADVISORY | Operational review. |
| Installation check | SICK | "No errors" — handover item | 🟡 ADVISORY | Flags config issues; rarely blocks the copy itself. |
| Mount point >80% | `df -h` | **Explicit exception pattern**: request acknowledgement, don't delete data | 🟡 ADVISORY | Documented threshold breach with a standard exception wording. Never OS-delete under HANA data volume. |
| Non-compliant parameters | RZ10 | Set-to-standard vs retain-custom → **exception template** | 🟡 ADVISORY | Customer decides; documented either way. |

**The rule, confirmed by the real process:**
> **BLOCK only what makes the copy fail technically or lose data. Everything that
> is system hygiene / go-live quality is ADVISORY, captured as a documented
> exception the customer acknowledges.**

---

## 6. Reporting & the audited-override model (already exists in the real process)

The real process **already implements audited override** — it's called the
**exception template**, and it is the direct blueprint for the toolkit's
exportable advisory report:

- **What it captures:** failed health checks, non-compliant parameters, custom
  requests, mount-point exceptions.
- **How a "decide to ignore" is documented:** the item is entered in the
  exception template with the finding, the chosen disposition (accept / retain
  custom / defer), and the responsible party — then the customer/provider
  **acknowledges** it. Nothing is ignored silently; every deviation leaves a
  trail.
- **Where detail lives:** screenshots and per-check evidence go in the Handover /
  Brownfield spreadsheet; the email references it rather than inlining a huge
  table.

**Status vocabulary** to reuse in the toolkit's report rows: `Open`, `In Process`,
`Completed`, `Not Ok`, `NA`, `Not required`, `Obsolete`. Color semantics from the
real Helper sheet: pass=green, fail/Not-Ok=red, skipped/NA=grey, in-process=yellow.

**Runbook status tags** (a lighter, live-progress vocabulary worth mirroring in
the TUI): `[OPEN]` (action required), `[OK]` (done), `[N/A]` (not applicable),
`[ERROR]` (problem found), `[REFERENCE]` (info only).

### Report structure the toolkit should emit (exportable advisory)
1. **Header:** system generic id, method, phase, window.
2. **Gate summary:** the three gates with GO/NO-GO/PENDING and criteria status.
3. **Per-phase table:** step | side | check | status | role | owner | note.
4. **Exceptions section:** every 🟡 advisory that was acknowledged/overridden —
   finding, disposition, who decided, when, why (the audit trail).
5. **Blocking section:** any 🔴 still open (should be empty to pass a gate).
6. **Evidence pointer:** reference to the detailed screenshot/log pack.

Format: clean, legible, printable (padded columns in terminal + downloadable) —
it is a **communication artifact that circulates with the customer by email**,
not just a console dump.

---

## 7. Design implications for the toolkit's gate engine

1. **Two gate policies, front-loaded.** Blocking checks concentrate at Gate #1
   (entry-to-downtime). Downtime itself is mostly monitors + Gate #2 verification.
2. **Severity is intrinsic (in the check); gate policy is per-engagement (config).**
   Add SIDE and RESPONSIBLE axes to every check — the same finding routes by owner
   and side.
3. **Override = auto-generated exception entry.** The override log *is* the
   handover exception template. It doubles as the sign-off artifact the operator
   has to produce anyway — so the tool saves work instead of adding ceremony.
4. **Advisory band stays narrow and curated** to avoid alert fatigue / bulk
   dismiss. INFO is not a gate — it's context on the panel.
5. **Two-role gate + one INFO layer.** BLOCK / ADVISORY are gate roles; INFO is
   display-only. The joint business GO/NO-GO (Gate #3) is outside tool authority —
   the tool informs it.
6. **The tool is a co-pilot, not a lock.** It renders a verdict and refuses to
   auto-advance a runbook on a blocking-open, but the operator can acknowledge and
   proceed — every override recorded. Supervised mode (junior executes senior's
   plan) can protect/disable override via config.

---

## 8. Summary table

| Check family | Phase | Txn/Cmd | Method(s) | Role | How reported |
|---|---|---|---|---|---|
| Credential validation | Preparation | — | ALL | 🔴 | Gate-1 criterion; blocking-open list |
| Recoverable backup <24h | Preparation | M_BACKUP_CATALOG | BR/EI | 🔴 | Gate-1 criterion |
| Target storage space | Preparation | df -h | ALL | 🔴 | Gate-1 criterion |
| Key-phrase/algorithm align | Preparation | checkKeyPhrase | BR/EI | 🔴 | Gate-1 criterion |
| Migration key present | Preparation | R3load/SWPM | EI | 🔴 | Gate-1 criterion |
| log_mode for method | Preparation | global.ini | HSR/TC | 🔴 | Gate-1 criterion |
| Architecture/byte-order | Preparation | lscpu | ALL | 🔴 | Gate-1 criterion |
| Critical file backup+checksum | Preparation | cp/md5 | BR/EI | 🔴 | Gate-1 criterion |
| Timezone consistency | Preparation | timedatectl | ALL | 🟡/🔴 | Advisory (config→block) |
| Encryption status | Preparation | M_ENCRYPTION_OVERVIEW | ALL | 🟡 | Exception report |
| ST22 dumps | Prep/Post | ST22 | ALL | 🟡 | Exception report |
| SE80 inactive objects | Prep/Post | SE80 | ALL | 🟡 | Exception report |
| SE14 temp tables | Post | SE14 | ALL | 🟡 | Exception report |
| SPAM queue | Post | SPAM | ALL | 🟡 | Exception report |
| SM21 system log | Post | SM21 | ALL | 🟡 | Exception report |
| SICK | Post | SICK | ALL | 🟡 | Exception report |
| Mount point >80% | Prep/Post | df -h | ALL | 🟡 | Exception report (std wording) |
| Non-compliant parameters | Post | RZ10 | ALL | 🟡 | Exception report |
| Stop app servers | Ramp-down/Downtime | — | ALL | 🔴 | Downtime monitor |
| User lock (exception list) | Ramp-down | SU10 | ALL | 🔴 | Gate-1 criterion |
| Pending updates/locks | Ramp-down | SM13/SM12/SMQ | ALL | 🟡 | Advisory-with-verify |
| Ramp-down complete | Ramp-down | — | ALL | 🔴 gate | **Gate #1** |
| SWPM export | Downtime | SWPM/R3load | EI | 🔴 | Downtime monitor |
| Transfer + checksum | Downtime | rsync/md5 | EI | 🔴 | Downtime monitor |
| HANA backup/restore | Downtime | HANA | BR | 🔴 | Downtime monitor |
| HSR SYNC before takeover | Downtime | hdbnsutil | HSR | 🔴 | Downtime monitor |
| SWPM import | Downtime | SWPM | EI | 🔴 | Downtime monitor |
| Parameter/profile switch | Post | RZ10/configtool | ALL | 🔴 | Gate-2 criterion |
| BDLS | Post | BDLS | ALL | 🔴 | Gate-2 criterion |
| STMS reconfigure | Post | STMS | ALL | 🟡 | Exception report |
| SGEN | Post | SGEN | ALL | 🟡 | Exception report |
| log_mode restore | Post | global.ini | ALL | 🔴 | Gate-2 criterion |
| Nodes green + logs clean | Post | NWA/SMLG | ALL | 🔴 | Gate-2 criterion |
| Tenant online + baseline backup | Post | HANA | ALL | 🔴 | Gate-2 criterion |
| Row-count consistency | Post | M_CS_TABLES | ALL | 🔴 | Gate-2 criterion (unexplained delta=NO-GO) |
| Inactive/unloaded tables | Post | M_CS_TABLES | ALL | 🟡 | Exception report |
| Table consistency | Post | CHECK_TABLE_CONSISTENCY | ALL | 🟡/🔴 | Advisory (real inconsistency→block) |
| Functional test | Testing | — | ALL | 🟡 | Recorded, not gated |
| Joint GO/NO-GO | Testing | — | ALL | 🔴 gate | **Gate #3 (out of tool authority)** |

---

*Model derived from anonymized real SAP migration cutover artifacts. Safe for
public repo: no customer, SID, host, person, URL, or schema identifiers included.*
