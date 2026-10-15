"""Shared helpers for the three MCP servers and the agent."""

from __future__ import annotations

import warnings

# FastMCP is a pydantic-settings model with a forward-referenced ``lifespan``
# field, so pydantic-settings emits IncompleteFieldDefinitionWarning on first
# use. It is noise (not an error), but MCP clients classify our stderr as
# ``[error]``, which makes the client log look alarming. Silence just that one
# warning class, keep everything else visible.
try:  # pragma: no cover - depends on pydantic-settings internals
    from pydantic_settings.sources.utils import IncompleteFieldDefinitionWarning
except Exception:  # pragma: no cover
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment]

if IncompleteFieldDefinitionWarning is not None:  # pragma: no cover
    warnings.filterwarnings("ignore", category=IncompleteFieldDefinitionWarning)