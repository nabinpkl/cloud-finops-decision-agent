"""FastAPI route for assistant-ui transport."""

from __future__ import annotations

from assistant_stream import create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import APIRouter, Request

from api.assistant_transport.models import AssistantRequest
from api.assistant_transport.state import apply_commands, prepare_incoming_state
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

    async def run_callback(controller) -> None:
        if not apply_commands(controller.state, body.commands):
            return
        await run_agent_turn(controller, session_id=session_id, hashed_id=hashed_id)

    stream = create_run(
        run_callback,
        state=prepare_incoming_state(body.state),
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

