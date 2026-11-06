"""Pure helpers for N-Box messages (no Home Assistant imports - easy to test).

Parsing of the Matter "Messages" attribute, the notification ID, the memory of
IDs that were already seen, and the decision "should this fire an event".
"""
from __future__ import annotations

import base64
import binascii
from collections import OrderedDict
from typing import Any

ID_LENGTH = 8  # bytes (16 hex characters)
SEEN_IDS_PER_ENDPOINT = 200

# (sender, app, content, notification id or None)
Message = tuple[str, str, str, "str | None"]


# ---------------------------------------------------------------------------
# Message text
# ---------------------------------------------------------------------------
def parse_message_text(text: Any) -> tuple[str, str, str]:
    """Split "sender|app|content" (only the first two "|" separate)."""
    parts = str(text).split("|", 2)
    if len(parts) == 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    # Not in the expected format: keep the text as the content
    return "Unknown", "Unknown", str(text).strip()


def _struct_field(struct: Any, name: str, *keys: Any) -> Any:
    """A field of a MessageStruct that is an object or a dict."""
    if isinstance(struct, dict):
        for key in keys:
            if key in struct and struct[key] is not None:
                return struct[key]
        return None
    return getattr(struct, name, None)


def _message_text(struct: Any) -> Any:
    return _struct_field(struct, "messageText", "messageText", "5", 5)


# ---------------------------------------------------------------------------
# Notification ID
# ---------------------------------------------------------------------------
def _id_bytes(raw: Any) -> bytes | None:
    """Raw ID value (any serialization seen so far) -> bytes, or None."""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    if isinstance(raw, (list, tuple)):
        if all(isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 255 for b in raw):
            return bytes(raw)
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if text[:4].lower() == "b64:":
            text = text[4:]
        # 16 hex characters = our ID. (8 bytes in base64 are 12 characters, so no mix-up.)
        if len(text) == ID_LENGTH * 2:
            try:
                return bytes.fromhex(text)
            except ValueError:
                pass
        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            return None
    return None


def parse_message_id(value: Any) -> str | None:
    """The notification ID of a MessageStruct as 16 lowercase hex characters.

    Accepts the struct (object, or dict with key "0" / 0 / "messageID"), the whole
    Messages attribute (list with the struct), or the raw ID itself. The raw ID may be
    bytes, a list of ints, a hex string or a base64 string.
    Returns None for: missing, wrong length, the legacy constant "1" (0x31, in any form),
    all zeros, or anything unexpected. Never raises.
    """
    try:
        if isinstance(value, (list, tuple)) and value and not all(isinstance(b, int) for b in value):
            value = value[0]  # the Messages attribute: take the first struct
        if isinstance(value, dict) or hasattr(value, "messageID"):
            raw = _struct_field(value, "messageID", "messageID", "0", 0)
        else:
            raw = value
        data = _id_bytes(raw)
        if data is None or len(data) != ID_LENGTH or not any(data):
            return None
        return data.hex()
    except Exception:  # noqa: BLE001 - never raise from a parser
        return None


# ---------------------------------------------------------------------------
# Whole attribute value
# ---------------------------------------------------------------------------
def parse_messages_full(value: Any) -> Message | None:
    """The Matter "Messages" attribute value -> (sender, app, content, id).

    * list with a message  -> the three parts and the ID (None when there is none)
    * empty list           -> notification cleared: ("None", "None", "None", None)
    * anything else        -> None (unknown shape, ignore)
    """
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) == 0:
        return ("None", "None", "None", None)
    struct = value[0]
    text = _message_text(struct)
    if text is None:
        return None
    sender, app, content = parse_message_text(text)
    return sender, app, content, parse_message_id(struct)


def parse_messages_value(value: Any) -> tuple[str, str, str] | None:
    """Same as parse_messages_full, without the ID (the original contract)."""
    message = parse_messages_full(value)
    return message[:3] if message else None


def is_clear(message: Message) -> bool:
    """True for the "cleared notification" message."""
    return message[0] == message[1] == message[2] == "None"


# ---------------------------------------------------------------------------
# Memory of IDs that were already seen
# ---------------------------------------------------------------------------
class SeenIds:
    """Per endpoint, the last N notification IDs (oldest are forgotten first)."""

    def __init__(self, limit: int = SEEN_IDS_PER_ENDPOINT) -> None:
        self.limit = limit
        self._ids: dict[int, OrderedDict[str, None]] = {}

    def __contains__(self, item: tuple[int, str]) -> bool:
        endpoint, message_id = item
        return message_id in self._ids.get(endpoint, ())

    def add(self, endpoint: int, message_id: str) -> None:
        ids = self._ids.setdefault(endpoint, OrderedDict())
        ids[message_id] = None
        ids.move_to_end(message_id)
        while len(ids) > self.limit:
            ids.popitem(last=False)

    def to_dict(self) -> dict[str, list[str]]:
        return {str(ep): list(ids) for ep, ids in self._ids.items()}

    @classmethod
    def from_dict(cls, data: Any, limit: int = SEEN_IDS_PER_ENDPOINT) -> SeenIds:
        seen = cls(limit)
        if not isinstance(data, dict):
            return seen
        for key, ids in data.items():
            try:
                endpoint = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(ids, list):
                continue
            for message_id in ids:
                if isinstance(message_id, str) and message_id:
                    seen.add(endpoint, message_id)
        return seen


def decide_event(
    seen: SeenIds,
    endpoint: int,
    message: Message,
    *,
    enabled: bool = True,
    silent: bool = False,
) -> bool:
    """True when this message must fire an "nbox_matter_message" event.

    Remembers the ID as a side effect (also when ``silent``, so the first read after a
    start does not announce history). Nothing fires, and nothing is remembered, for a
    disabled endpoint; nothing fires for a message without ID or a cleared one.
    """
    if not enabled:
        return False
    message_id = message[3]
    if message_id is None or is_clear(message):
        return False
    if (endpoint, message_id) in seen:
        return False
    seen.add(endpoint, message_id)
    return not silent
