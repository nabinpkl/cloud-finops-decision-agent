"""AG-UI transport encoding for the assistant surface (ADR-0016).

The backend is an AG-UI server: it streams AG-UI protocol events
(``RUN_STARTED``/``RUN_FINISHED``, text/tool message events, and
``STATE_SNAPSHOT``/``STATE_DELTA`` for the backend-authoritative view-state)
over Server-Sent Events. The neutral runtime ``Emitter`` verbs (ADR-0012) are
unchanged; a single AG-UI encoder maps those verbs plus state mutations onto
AG-UI wire events. No price/citation/claim-binding logic lives here; the
hardening surface (escaping, limits, judge, AnswerPlan rendering, budgets)
stays in the turn orchestration above this layer.
"""

from __future__ import annotations

from api.assistant_transport.agui.context import AGUIRunContext
from api.assistant_transport.agui.emitter import AGUIStateEmitter

__all__ = ["AGUIRunContext", "AGUIStateEmitter"]
