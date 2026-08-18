"""Canonical Supervisor authority identity.

This dependency-free leaf is shared by effectful low-level APIs. Only the
exact Supervisor controller identity may create or mutate governed state.
"""
from __future__ import annotations

SUPERVISOR_CONTROLLER = "supervisor"
