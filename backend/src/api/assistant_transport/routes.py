"""FastAPI route for assistant-ui transport."""

from __future__ import annotations

from assistant_stream import create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import APIRouter, HTTPException, Request

from api.assistant_transport.models import AssistantRequest
from api.assistant_transport.state import (
    apply_commands,
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
) -> DataStreamResponse:
    session_id = request.cookies.get(settings.session_cookie_name) or new_session_id()
    hashed_id = getattr(request.state, "hashed_client_id", "") or ""
    state = prepare_incoming_state(body.state)
    triggered_by_user_message = apply_commands(state, body.commands)
    if history_text_length(state) > settings.assistant_max_history_chars:
        raise HTTPException(
            status_code=422,
            detail="assistant history exceeds configured character limit",
        )

    async def run_callback(controller) -> None:
        if not triggered_by_user_message:
            return
        await run_agent_turn(controller, session_id=session_id, hashed_id=hashed_id)

    stream = create_run(
        run_callback,
        state=state,
    )
    response = DataStreamResponse(stream)
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
