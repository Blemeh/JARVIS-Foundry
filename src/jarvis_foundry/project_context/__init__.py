"""Durable, bounded, read-only project intelligence.

Public consumers should use :class:`ProjectProfileService` for discovery and
research, or :class:`ProjectContextResolver` for the A3 planning bridge.  No
surface in this package admits work or executes a discovered command.
"""

from .contracts import (
    CONTEXT_SCHEMA,
    PROFILE_SCHEMA,
    UNKNOWN,
    ProfileResolution,
    ProfileStatus,
    ProjectProfile,
    ProjectTarget,
)
from .inspector import ProjectInspectionError, ProjectInspector
from .service import (
    ProjectContextResolver,
    ProjectProfileIdentityError,
    ProjectProfileProvider,
    ProjectProfileService,
    ProjectProfileStore,
    ProjectProfileStoreError,
)

__all__ = [
    "CONTEXT_SCHEMA",
    "PROFILE_SCHEMA",
    "UNKNOWN",
    "ProfileResolution",
    "ProfileStatus",
    "ProjectContextResolver",
    "ProjectInspectionError",
    "ProjectInspector",
    "ProjectProfile",
    "ProjectProfileIdentityError",
    "ProjectProfileProvider",
    "ProjectProfileService",
    "ProjectProfileStore",
    "ProjectProfileStoreError",
    "ProjectTarget",
]
