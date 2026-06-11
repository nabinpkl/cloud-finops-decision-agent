"""FastAPI route for the assistant surface, served as an AG-UI server (ADR-0016).

``POST /assistant`` accepts an AG-UI ``RunAgentInput`` body (the shape
``@ag-ui/client``'s ``HttpAgent`` sends: ``{threadId, runId, state, messages,
tools, context, forwardedProps}`` with the user's text in ``messages``). It
emits AG-UI protocol events over Server-Sent Events: ``RUN_STARTED``, the
streamed text/tool events for the turn, a final ``STATE_SNAPSHOT`` carrying the
backend-authoritative view-state, and ``RUN_FINISHED``. The agent runtime port
(ADR-0012) is untouched: adapters stream neutral ``Emitter`` verbs and a single
AG-UI encoder (``AGUIStateEmitter``) maps them onto AG-UI events. The hardening
surface (body limit, history limit, XML wrapping, input judge, AnswerPlan
rendering, budgets) stays in the turn orchestration; nothing moved to the wire
layer.
"""

from __future__ import annotations

import uuid

from ag_ui.core import (
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.assistant_transport.agui import AGUIRunContext
from api.assistant_transport.models import AssistantRequest
from api.assistant_transport.state import (
    apply_agui_messages,
    history_text_length,
    prepare_incoming_state,
)
from api.assistant_transport.turn import run_agent_turn
from api.budget.identity import new_session_id
from app_config import settings

router = APIRouter()


@router.post("/assistant")
async def assistant_endpoint(
    body: AssistantRequest,
    request: Request,
) -> StreamingResponse:
    session_id = request.cookies.get(settings.session_cookie_name) or new_session_id()
    hashed_id = getattr(request.state, "hashed_client_id", "") or ""
    state = prepare_incoming_state(body.state)
    # The frontend sends the full conversation in ``messages`` (RunAgentInput).
    # The backend is authoritative over view-state; messages only seed the turn.
    triggered_by_user_message = apply_agui_messages(state, body.messages)
    if history_text_length(state) > settings.assistant_max_history_chars:
        raise HTTPException(
            status_code=422,
            detail="assistant history exceeds configured character limit",
        )

    ctx = AGUIRunContext(state)
    thread_id = request.cookies.get(settings.session_cookie_name) or session_id
    run_id = f"run_{uuid.uuid4().hex}"

    # Run the turn to completion before streaming. The policy layer buffers the
    # final answer until it validates (ADR-0013), so streaming-as-we-go vs
    # buffer-then-stream is observationally identical for the hardening surface:
    # no unvalidated price text ever reaches the wire either way.
    if triggered_by_user_message:
        await run_agent_turn(ctx, session_id=session_id, hashed_id=hashed_id)
    ctx.close()

    encoder = EventEncoder(accept=request.headers.get("accept", ""))

    async def event_stream():
        yield encoder.encode(RunStartedEvent(thread_id=thread_id, run_id=run_id))
        async for event in ctx.drain():
            yield encoder.encode(event)
        # Backend-authoritative view-state: broadcast the settled snapshot so
        # the frontend renders messages + view + selection + the server-trusted
        # sessionLimitReached flag.
        yield encoder.encode(StateSnapshotEvent(snapshot=ctx.state))
        yield encoder.encode(RunFinishedEvent(thread_id=thread_id, run_id=run_id))

    response = StreamingResponse(
        event_stream(),
        media_type=encoder.get_content_type(),
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=settings.session_idle_timeout_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return response
