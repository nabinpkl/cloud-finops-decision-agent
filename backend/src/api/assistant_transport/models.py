"""Assistant transport request models.

The shipped frontend talks AG-UI: ``@ag-ui/client``'s ``HttpAgent`` POSTs a
``RunAgentInput`` body (``{threadId, runId, state, messages, tools, context,
forwardedProps, ...}``) where the user's text lives in ``messages`` and there is
no ``commands`` field. The legacy assistant-ui transport instead posted
``{state, commands:[...]}`` with the new user message inside ``commands``.

This module accepts BOTH shapes so the same endpoint serves the AG-UI frontend
and the legacy contract. The route normalizes either shape into the
backend-authoritative state before any turn runs; the hardening surface (text
length caps, message-part caps, history cap, XML wrapping) applies identically
to both. ``extra='ignore'`` drops AG-UI envelope fields the backend does not
read (``threadId``/``runId``/``context``/``forwardedProps``); they are not
trusted inputs.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app_config import settings


class MessagePart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    text: str | None = Field(default=None, max_length=settings.assistant_max_text_chars)


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["user"] = "user"
    parts: list[MessagePart] = Field(
        min_length=1,
        max_length=settings.assistant_max_message_parts,
    )


class AddMessageCommand(BaseModel):
    type: Literal["add-message"] = "add-message"
    message: UserMessage


class AddToolResultCommand(BaseModel):
    type: Literal["add-tool-result"] = "add-tool-result"
    toolCallId: str = Field(max_length=256)
    result: dict[str, Any]


Command = Annotated[
    AddMessageCommand | AddToolResultCommand, Field(discriminator="type")
]


class AGUIMessage(BaseModel):
    """One AG-UI ``RunAgentInput.messages[]`` entry.

    AG-UI messages carry ``{id, role, content}`` where ``content`` for a user
    message is a plain string (it may also be a multimodal parts array, which
    this public pricing agent does not accept — only the string form is read).
    ``extra='ignore'`` drops ``toolCalls``/``name``/``encryptedValue`` etc.
    """

    model_config = ConfigDict(extra="ignore")

    role: str
    content: Any | None = None


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # AG-UI RunAgentInput carries the conversation here; the latest user message
    # is the turn trigger. Optional so the legacy ``commands`` shape still works.
    messages: list[AGUIMessage] | None = Field(
        default=None, max_length=settings.assistant_max_state_messages
    )
    # Legacy assistant-ui transport shape. Optional now: an AG-UI request has no
    # ``commands`` field, so requiring it 422'd every real frontend request.
    commands: list[Command] | None = Field(
        default=None, max_length=settings.assistant_max_commands
    )
    # Backend-authoritative view-state is reseeded server-side; a client-supplied
    # ``state`` is accepted only for its ``messages`` history and immediately
    # stripped of any client view/selection/enforcement flags.
    state: dict[str, Any] | None = None
    # AG-UI / legacy envelope fields the backend does not trust or read. Declared
    # so they are accepted (then ignored) rather than rejected; ``tools`` is a
    # list in AG-UI and a dict in the legacy shape, so it is typed ``Any``.
    tools: Any | None = None
    context: Any | None = None
    forwardedProps: Any | None = None
    system: str | None = None
    runConfig: dict[str, Any] | None = None
