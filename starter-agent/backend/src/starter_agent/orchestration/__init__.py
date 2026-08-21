"""Explicit execution-orchestration control-plane contracts.

This package does not contain another Agent Runtime.  It defines the state and
decision contracts used to route work into the existing runtime, workflows and
delegation services.
"""

from starter_agent.orchestration.models import ExecutionState

__all__ = ["ExecutionState"]

