"""Shared, side-effect-free helpers for the Export/Import (R3load/JLoad) method.

Everything here builds ``argv: list[str]`` command lines (never a shell string)
or is a **pure parser** of the R3load load tools' log output — so it works on
BOTH a local ``Runner`` and a remote ``SSHRunner`` (Exodia's hard safety rule)
and is fully unit-testable with no runner and no SAP system.

Design decision (fixed): Exodia is a THIN ORCHESTRATOR of SWPM/sapinst. It does
NOT reimplement R3load/JLoad. Its value is (1) orchestrating the sapinst export
and import in a guarded way and (2) MONITORING the R3load/migration-monitor logs
(``export_monitor.log`` / ``import_monitor.log``, ``*.TSK`` task files) to give
progress, failed-package detection, and post-import integrity.

The real, load-independent log conventions parsed here (SAP migration monitor /
R3load):

* The migration monitor writes one line per package to ``export_monitor.log`` /
  ``import_monitor.log`` such as::

      INFO: 2026-... 'SAPAPPL0' export finished with rc == 0.
      ERROR: 2026-... 'SAPSSEXC' export with error, rc == 2.
      INFO: 2026-... 'SAPAPPL0' import took 0:12:31, rc == 0.

* R3load ``*.TSK`` task files list one entry per object with a trailing status
  token — ``ok`` (done), ``err`` (failed) or ``xeq`` (to execute / pending)::

      D SAPAPPL0 I ok
      T "/BI0/AABC~0" I err

References (cite by number only, never reproduce Note text): SAP Note 784118
(system copy / R3load tools), 1738258 (SWPM), 82478 (OS/DB migration key),
43853 / 745030 (Unicode), 1043380 (package splitter / table splitting),
588668 (BRCONNECT update statistics).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field

from exodia.core.params import ParamSpec

# --------------------------------------------------------------------------- #
# Parameter specs — declared by the checks/actions so the interactive menu can
# prompt for exactly the inputs the method needs. Generic placeholders only
# (SID 'ABC', host 'host1', instance '00') — never real customer data.
# --------------------------------------------------------------------------- #

STACK = ParamSpec(
    "stack", "System stack", choices=("abap", "java", "dual"), default="abap",
    help="ABAP uses R3load; AS Java uses JLoad; dual-stack needs both.",
)
SWPM_PATH = ParamSpec(
    "swpm_path", "SWPM directory", default="/usr/sap/SWPM",
    help="Directory containing the sapinst executable.",
)
SAPINST_PATH = ParamSpec(
    "sapinst_path", "sapinst executable", default="/usr/sap/SWPM/sapinst",
    help="Full path to the sapinst binary that drives the export/import.",
)
INIFILE = ParamSpec(
    "inifile", "SWPM inifile.params path",
    help="Existing inifile.params (Ansible sap_swpm convention). Exodia validates/reuses it.",
)
PRODUCT_ID = ParamSpec(
    "product_id", "SAPINST product id",
    help="SAPINST_EXECUTE_PRODUCT_ID for the export (source) or import (target) step.",
)
EXPORT_DIR = ParamSpec(
    "export_dir", "Export dump directory", default="/export",
    help="Where R3load/JLoad write (source) or read (target) the export dump.",
)
IMPORT_DIR = ParamSpec(
    "import_dir", "Import source directory", default="/export",
    help="Directory on the target the import reads the dump from.",
)
EXPORT_MONITOR_LOG = ParamSpec(
    "export_monitor_log", "export_monitor.log path",
    help="Path to the migration monitor's export_monitor.log to parse for progress.",
)
IMPORT_MONITOR_LOG = ParamSpec(
    "import_monitor_log", "import_monitor.log path",
    help="Path to the migration monitor's import_monitor.log to parse for progress.",
)
TARGET_DATA_DIR = ParamSpec(
    "target_data_dir", "Target DB data directory",
    help="Filesystem holding the target database data files (import destination).",
)
IMPORT_SIZE_GB = ParamSpec(
    "import_size_gb", "Expected imported size (GB)",
    help="Approx target DB size after import; used to check free space.",
)
MIGRATION_KEY = ParamSpec(
    "migration_key", "SAP migration key (heterogeneous system copy)",
    help="The OS/DB migration key from SAP for a heterogeneous system copy (SAP Note 82478). "
    "Not a password — a license token gating a heterogeneous R3load export.",
)
SOURCE_DB = ParamSpec(
    "source_db", "Source database platform",
    choices=("hana", "ase", "oracle", "db2", "maxdb", "sybase", "mssql"),
    help="The database the export is taken FROM.",
)
TARGET_DB = ParamSpec(
    "target_db", "Target database platform",
    choices=("hana", "ase", "oracle", "db2"),
    help="The database the import goes INTO.",
)
SOURCE_UNICODE = ParamSpec(
    "source_unicode", "Source is Unicode (true/false)", default="true",
    help="Whether the SOURCE system is Unicode. Non-Unicode implies a Unicode conversion.",
)
TARGET_UNICODE = ParamSpec(
    "target_unicode", "Target is Unicode (true/false)", default="true",
    help="Whether the TARGET system is Unicode. Modern NetWeaver targets are Unicode-only.",
)
SPLIT_DIR = ParamSpec(
    "split_dir", "Table-splitting output directory",
    help="Directory holding the R3ta / package-splitter output (WHR/STR/*.txt) for large tables.",
)

# --------------------------------------------------------------------------- #
# Migration-key validation — pure.
# --------------------------------------------------------------------------- #

# A migration key is a 24-char base-32-ish token (letters+digits). We validate
# shape only; SAP owns the real cryptographic check inside R3load.
_MIGRATION_KEY_RE = re.compile(r"^[A-Za-z0-9]{20,30}$")


def is_valid_migration_key(value: str | None) -> bool:
    """True when the migration key is present and shaped like a real one."""
    return bool(value) and bool(_MIGRATION_KEY_RE.match(str(value).strip()))


def is_heterogeneous(source_db: str | None, target_db: str | None) -> bool:
    """A heterogeneous (OS/DB) copy = the source and target DB platforms differ."""
    if not source_db or not target_db:
        return False
    return source_db.strip().lower() != target_db.strip().lower()


def as_bool(value: object, default: bool = False) -> bool:
    """Interpret a param value as a boolean (true/yes/y/1/on)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("true", "yes", "y", "1", "on")


# --------------------------------------------------------------------------- #
# Migration-monitor log parsing — pure, deterministic, no runner.
# --------------------------------------------------------------------------- #

# 'PKG' export/import finished with rc == 0.   -> success
# 'PKG' export/import with error, rc == 2.     -> failure
# 'PKG' import took 0:12:31, rc == 0.          -> success + duration
_PKG_OK_RE = re.compile(
    r"'(?P<pkg>[^']+)'\s+(?:export|import)\b[^\n]*?rc\s*==\s*0\b",
    re.IGNORECASE,
)
_PKG_ERR_RE = re.compile(
    r"'(?P<pkg>[^']+)'\s+(?:export|import)\b[^\n]*?(?:with error|rc\s*==\s*[1-9]\d*)",
    re.IGNORECASE,
)
# Duration like "took 0:12:31" or "took 12 min".
_PKG_TOOK_RE = re.compile(
    r"'(?P<pkg>[^']+)'\s+(?:export|import)\s+took\s+(?P<dur>[0-9:]+(?:\s*min)?)",
    re.IGNORECASE,
)
# A package still executing (migration monitor prints a running summary).
_STILL_RUNNING_RE = re.compile(r"(?P<n>\d+)\s+(?:jobs?|packages?)\s+(?:still\s+)?running", re.IGNORECASE)
# Explicit error/severe lines that are NOT tied to a package name.
_SEVERE_RE = re.compile(r"^\s*(?:ERROR|FATAL|SEVERE)\b", re.IGNORECASE)


@dataclass
class MonitorReport:
    """Parsed snapshot of a migration monitor log (export or import)."""

    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    durations: dict[str, str] = field(default_factory=dict)
    running: int = 0
    error_lines: list[str] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        """Distinct packages seen (completed OK or failed)."""
        return len(set(self.completed) | set(self.failed))

    @property
    def progress_pct(self) -> float | None:
        """Completed / total-seen percentage, or None when nothing is seen."""
        total = self.total_seen
        if total == 0:
            return None
        return max(0.0, min(100.0, 100.0 * len(set(self.completed)) / total))

    @property
    def has_errors(self) -> bool:
        return bool(self.failed) or bool(self.error_lines)


def parse_monitor_log(text: str) -> MonitorReport:
    """Parse an ``export_monitor.log`` / ``import_monitor.log`` into a report.

    A package that reports ``rc == 0`` is completed; ``with error`` or a non-zero
    rc is a failure. Failure precedence wins: a package that ever failed is not
    counted as completed even if a later retry line also matched. Bare
    ERROR/FATAL/SEVERE lines are collected as ``error_lines`` for context.
    """
    report = MonitorReport()
    failed_set: set[str] = set()
    completed_set: set[str] = set()
    for raw in text.splitlines():
        line = raw.rstrip()
        m_err = _PKG_ERR_RE.search(line)
        if m_err:
            failed_set.add(m_err.group("pkg"))
            report.error_lines.append(line.strip())
            continue
        m_ok = _PKG_OK_RE.search(line)
        if m_ok:
            completed_set.add(m_ok.group("pkg"))
        m_took = _PKG_TOOK_RE.search(line)
        if m_took:
            report.durations[m_took.group("pkg")] = m_took.group("dur").strip()
        m_run = _STILL_RUNNING_RE.search(line)
        if m_run:
            with contextlib.suppress(ValueError):
                report.running = int(m_run.group("n"))
        if _SEVERE_RE.match(line) and not m_err:
            report.error_lines.append(line.strip())
    # Failure wins over a stray success line for the same package.
    completed_set -= failed_set
    report.completed = sorted(completed_set)
    report.failed = sorted(failed_set)
    return report


# --------------------------------------------------------------------------- #
# R3load *.TSK task-file parsing — pure.
# --------------------------------------------------------------------------- #

# A TSK line ends with a status token: ok / err / xeq. Leading columns are the
# object type (D/T/P/V/I), the object name (possibly quoted), and the action.
_TSK_LINE_RE = re.compile(r"\b(?P<status>ok|err|xeq)\s*$", re.IGNORECASE)


@dataclass
class TskReport:
    """Parsed counts from one or more R3load ``*.TSK`` task files."""

    ok: int = 0
    err: int = 0
    xeq: int = 0

    @property
    def total(self) -> int:
        return self.ok + self.err + self.xeq

    @property
    def all_ok(self) -> bool:
        """Every task done and none failed / pending."""
        return self.total > 0 and self.err == 0 and self.xeq == 0


def parse_tsk(text: str) -> TskReport:
    """Parse R3load ``*.TSK`` content, counting ok / err / xeq statuses."""
    report = TskReport()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _TSK_LINE_RE.search(line)
        if not m:
            continue
        status = m.group("status").lower()
        if status == "ok":
            report.ok += 1
        elif status == "err":
            report.err += 1
        elif status == "xeq":
            report.xeq += 1
    return report


# --------------------------------------------------------------------------- #
# Row-count manifest parsing (data-consistency source vs target) — pure.
# --------------------------------------------------------------------------- #


def parse_count_manifest(text: str) -> dict[str, int]:
    """Parse a ``table,count`` manifest (CSV-ish) into a dict.

    Tolerant: skips blanks, comments (#) and malformed lines. Whitespace or
    comma separated, e.g. ``MARA 1234`` or ``MARA,1234``.
    """
    counts: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[,;\s]+", line)
        if len(parts) < 2:
            continue
        table = parts[0].strip().strip('"').upper()
        try:
            counts[table] = int(parts[1])
        except ValueError:
            continue
    return counts


@dataclass
class CountDiff:
    """Result of comparing two row-count manifests."""

    common: int = 0
    matches: int = 0
    mismatches: dict[str, tuple[int, int]] = field(default_factory=dict)  # table -> (src, tgt)
    only_source: list[str] = field(default_factory=list)
    only_target: list[str] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return not self.mismatches and not self.only_source


def compare_counts(source: dict[str, int], target: dict[str, int]) -> CountDiff:
    """Compare source vs target row counts. A source table missing on the target,
    or a lower target count, is a data-loss signal."""
    diff = CountDiff()
    src_tables = set(source)
    tgt_tables = set(target)
    common = sorted(src_tables & tgt_tables)
    diff.common = len(common)
    for t in common:
        if source[t] == target[t]:
            diff.matches += 1
        else:
            diff.mismatches[t] = (source[t], target[t])
    diff.only_source = sorted(src_tables - tgt_tables)
    diff.only_target = sorted(tgt_tables - src_tables)
    return diff
