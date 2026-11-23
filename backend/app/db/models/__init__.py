"""SQLAlchemy models. Import every model module here so Alembic autogenerate sees all tables."""

from app.db.base import Base
from app.db.models.contact import DirectConversation, Friendship, FriendshipStatus, UserBan
from app.db.models.message import MESSAGE_MAX_BYTES, Message
from app.db.models.room import (
    ChatReadState,
    InvitationStatus,
    Room,
    RoomBan,
    RoomInvitation,
    RoomMember,
    RoomVisibility,
)
from app.db.models.user import PasswordResetToken, User, UserSession

__all__ = [
    "Base",
    "ChatReadState",
    "DirectConversation",
    "Friendship",
    "FriendshipStatus",
    "InvitationStatus",
    "MESSAGE_MAX_BYTES",
    "Message",
    "PasswordResetToken",
    "Room",
    "RoomBan",
    "RoomInvitation",
    "RoomMember",
    "RoomVisibility",
    "User",
    "UserBan",
    "UserSession",
]
