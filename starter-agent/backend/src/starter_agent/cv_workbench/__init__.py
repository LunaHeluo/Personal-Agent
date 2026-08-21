"""CV workbench domain contracts.

The package starts as a dependency-light contract boundary. Runtime, storage,
HTTP routing, and frontend integration are introduced by later tasks.
"""

from starter_agent.cv_workbench.contracts import CONTRACT_VERSION

__all__ = ["CONTRACT_VERSION"]
