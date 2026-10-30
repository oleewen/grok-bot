from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Session:
    id: str
    name: str
    chat_type: int
    group: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "chatType": self.chat_type,
            "group": self.group,
        }


@dataclass
class Message:
    msg_id: str
    sender: str
    sender_id: str
    body: str
    send_time: Optional[int]
    chat_type: Optional[int]
    session_id: str
    session_name: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "msgId": self.msg_id,
            "sender": self.sender,
            "senderId": self.sender_id,
            "body": self.body,
            "sendTime": self.send_time,
            "chatType": self.chat_type,
            "sessionId": self.session_id,
            "sessionName": self.session_name,
            "source": self.source,
        }
