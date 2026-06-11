"""Backend-authoritative run context for the AG-UI transport.

``AGUIRunContext`` replaces ``assistant_stream``'s ``RunController`` as the
single owner of the canonical view-state during one turn. The agent runtime
and the manual form both mutate this state through the backend; the route
broadcasts ``STATE_SNAPSHOT`` on connect and ``STATE_DELTA`` (or a fresh
snapshot) after the turn settles. The frontend renders state; it does not own
it (ADR-0016 decision 3).

The context exposes the same ``state`` attribute the previous transport used,
so the hardening-surface helpers (``state.py`` message helpers, ``StateEmitter``
mapping) work unchanged. State mutation logic does not move to the wire layer.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ag_ui.core import BaseEvent


class AGUIRunContext:
    """Owns the view-state and an async queue of AG-UI events for one turn."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self._events: asyncio.Queue[BaseEvent | None] = asyncio.Queue()

    def emit_event(self, event: BaseEvent) -> None:
        """Queue an AG-UI event for the SSE stream."""
        self._events.put_nowait(event)

    def close(self) -> None:
        """Signal the stream that no more events will arrive."""
        self._events.put_nowait(None)

    async def drain(self):
        """Yield queued events until ``close()`` is signalled."""
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event
