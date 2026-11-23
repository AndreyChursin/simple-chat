"""Client → server WebSocket events (docs/API_AND_WS_SPEC.md §12.2)."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRefIn(_Event):
    type: Literal["room", "dm"]
    id: int = Field(ge=1)


class Heartbeat(_Event):
    type: Literal["heartbeat"]


class Activity(_Event):
    type: Literal["activity"]


class Typing(_Event):
    type: Literal["typing"]
    chat: ChatRefIn


class SendMessage(_Event):
    type: Literal["send_message"]
    req_id: str | None = Field(None, max_length=64)
    chat: ChatRefIn
    client_msg_id: uuid.UUID | None = None
    content: str = ""
    reply_to_message_id: int | None = None


ClientEvent = Annotated[Heartbeat | Activity | Typing | SendMessage, Field(discriminator="type")]
client_event: TypeAdapter[Heartbeat | Activity | Typing | SendMessage] = TypeAdapter(ClientEvent)
