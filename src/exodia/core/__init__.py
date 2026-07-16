"""Core infrastructure shared by every module: context, shell, checks, actions, KB, reporting."""

from .base import Action, Check
from .context import Context
from .result import Result, Status

__all__ = ["Action", "Check", "Context", "Result", "Status"]
