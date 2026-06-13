"""Assistant transport request models.

The frontend talks AG-UI: ``@ag-ui/client``'s ``HttpAgent`` POSTs a
``RunAgentInput`` body (``{threadId, runId, state, messages, tools, context,
forwardedProps, ...}``) where the user's text lives in ``messages``. This module
models only the fields the backend reads (``messages`` and ``state``); every
other AG-UI envelope field is dropped by ``extra='ignore'`` and never trusted.
The route normalizes ``messages`` into the backend-authoritative state before any
turn runs; the hardening surface (text length cap, message cap, history cap, XML
wrapping) applies before the model is called.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app_config import settings


class AGUIMessage(BaseModel):
    """One AG-UI ``RunAgentInput.messages[]`` entry.

    AG-UI messages carry ``{id, role, content}`` where ``content`` for a user
    message is a plain string (it may also be a multimodal parts array, which
    this public pricing agent does not accept — only the string form is read).
    ``extra='ignore'`` drops ``toolCalls``/``name``/``encryptedValue`` etc.
    Oversize string content is rejected (422), not silently truncated, so the
    input cap is a visible contract exactly like the deterministic routes.
    """

    model_config = ConfigDict(extra="ignore")

    role: str
    content: Any | None = None

    @field_validator("content")
    @classmethod
    def _content_within_cap(cls, value: Any) -> Any:
        if isinstance(value, str) and len(value) > settings.assistant_max_text_chars:
            raise ValueError(
                "message content exceeds "
                f"{settings.assistant_max_text_chars} characters"
            )
        return value


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # AG-UI RunAgentInput carries the conversation here; the latest user message
    # is the turn trigger. Required: a request with no messages is not a turn.
    messages: list[AGUIMessage] = Field(
        min_length=1, max_length=settings.assistant_max_state_messages
    )
    # A client-supplied ``state`` is accepted only for round-trip scaffolding and
    # is immediately stripped of any client view/selection/enforcement flags;
    # view-state is backend-authoritative.
    state: dict[str, Any] | None = None
    # AG-UI RunAgentInput.forwardedProps: an untrusted, optional grounding
    # channel the frontend uses to forward the manual dashboard's current view
    # ({"currentView": {vcpu, ram_gb, family, region}}). It is NOT authoritative:
    # the route re-validates it through CompareQueryArgs and drops anything
    # malformed; it never seeds view-state and is never persisted.
    forwarded_props: dict[str, Any] | None = Field(
        default=None, alias="forwardedProps"
    )
