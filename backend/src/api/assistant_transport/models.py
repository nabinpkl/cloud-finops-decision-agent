"""assistant-ui transport request models."""

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


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    commands: list[Command] = Field(max_length=settings.assistant_max_commands)
    system: str | None = None
    tools: dict[str, Any] | None = None
    runConfig: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
