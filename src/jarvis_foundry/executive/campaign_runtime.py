"""Compatibility import for the V12 campaign aggregate.

The implementation lives in :mod:`jarvis_foundry.executive.campaigns`;
callers may use either descriptive module name without creating a second
runtime authority.
"""

from .campaigns import *  # noqa: F401,F403

