"""Provider-neutral email tool suite primitives."""

from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import (
    EmailCapabilities,
    EmailMessage,
    EmailMessageSummary,
    EmailSearchPage,
    EmailSearchQuery,
)

__all__ = [
    "EmailCapabilities",
    "EmailError",
    "EmailErrorCode",
    "EmailMessage",
    "EmailMessageSummary",
    "EmailSearchPage",
    "EmailSearchQuery",
]
