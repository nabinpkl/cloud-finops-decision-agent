"""assistant-ui transport request models."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class MessagePart(BaseModel):
    type: str
    text: str | None = None


class UserMessage(BaseModel):
    role: str = "user"
    parts: list[MessagePart]


class AddMessageCommand(BaseModel):
    type: Literal["add-message"] = "add-message"
    message: UserMessage


class AddToolResultCommand(BaseModel):
    type: Literal["add-tool-result"] = "add-tool-result"
    toolCallId: str
    result: dict[str, Any]


Command = Annotated[
    AddMessageCommand | AddToolResultCommand, Field(discriminator="type")
]


class AssistantRequest(BaseModel):
    commands: list[Command]
    system: str | None = None
    tools: dict[str, Any] | None = None
    runConfig: dict[str, Any] | None = None
    state: dict[str, Any] | None = None

