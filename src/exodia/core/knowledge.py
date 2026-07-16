"""Embedded troubleshooting knowledge base.

Exodia is self-sufficient: when a check/action fails, it matches the error
against a static KB shipped in the repo and surfaces cause + fix + SAP Note.
No RAG, no LLM, no dependency on any other project.

IP rule: we reference SAP Note *numbers* and generic public-knowledge fixes.
We never reproduce SAP Note text (copyright SAP, behind login).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .context import Context
    from .result import Result

_ERRORS_DIR = Path(__file__).parent.parent / "knowledge" / "errors"


class KBEntry:
    """A single known-error → remediation mapping."""

    def __init__(self, raw: dict) -> None:
        self.pattern = re.compile(raw["pattern"], re.IGNORECASE)
        self.cause: str = raw.get("cause", "")
        self.fix: list[str] = raw.get("fix", [])
        self.sap_note: str | None = raw.get("sap_note")


@lru_cache(maxsize=1)
def _load_kb() -> list[KBEntry]:
    entries: list[KBEntry] = []
    if not _ERRORS_DIR.exists():
        return entries
    for yml in sorted(_ERRORS_DIR.glob("*.yaml")):
        data = yaml.safe_load(yml.read_text()) or []
        entries.extend(KBEntry(item) for item in data)
    return entries


def lookup(text: str) -> KBEntry | None:
    """Return the first KB entry whose pattern matches the given text."""
    for entry in _load_kb():
        if entry.pattern.search(text):
            return entry
    return None


def enrich(result: Result, ctx: Context | None = None) -> Result:
    """Attach cause/fix/sap_note to a failing result from the KB, in place."""
    haystack = f"{result.summary}\n{result.detail}"
    match = lookup(haystack)
    if match:
        result.cause = result.cause or match.cause
        result.fix = result.fix or match.fix
        result.sap_note = result.sap_note or match.sap_note
    return result
