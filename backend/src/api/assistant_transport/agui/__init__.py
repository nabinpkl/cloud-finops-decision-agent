"""AG-UI transport encoding for the assistant surface (ADR-0016).

The backend is an AG-UI server: it streams AG-UI protocol events
(``RUN_STARTED``/``RUN_FINISHED``, text/tool message events, and a final
``STATE_SNAPSHOT`` for the backend-authoritative view-state) over Server-Sent
Events. The transport is snapshot-only: the turn buffers mutations and emits one
full ``STATE_SNAPSHOT`` after it settles; no ``STATE_DELTA`` is emitted (a full
replace is observationally equivalent for a single-turn round-trip, and avoids
shipping partial unvalidated state). The neutral runtime ``Emitter`` verbs
(ADR-0012) are unchanged; a single AG-UI encoder maps those verbs plus state
mutations onto AG-UI wire events. No price/citation/claim-binding logic lives
here; the hardening surface (escaping, limits, judge, AnswerPlan rendering,
budgets) stays in the turn orchestration above this layer.
"""

from __future__ import annotations

from api.assistant_transport.agui.context import AGUIRunContext
from api.assistant_transport.agui.emitter import AGUIStateEmitter

__all__ = ["AGUIRunContext", "AGUIStateEmitter"]
