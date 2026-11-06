"""Tests for the pure helpers in custom_components/nbox_matter/messages.py.

The module is loaded by path so that Home Assistant does not need to be installed.
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_PATH = Path(__file__).parent.parent / "custom_components" / "nbox_matter" / "messages.py"
_spec = importlib.util.spec_from_file_location("nbox_messages", _PATH)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

RAW = bytes.fromhex("0123456789abcdef")
HEX = "0123456789abcdef"


def struct(text="Anna|WhatsApp|Hi", message_id=RAW, as_dict=True):
    if as_dict:
        data = {"5": text}
        if message_id is not None:
            data["0"] = message_id
        return data
    return SimpleNamespace(messageText=text, messageID=message_id)


# --- parse_message_id --------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        RAW,
        bytearray(RAW),
        list(RAW),
        tuple(RAW),
        HEX,
        HEX.upper(),
        base64.b64encode(RAW).decode(),
        "b64:" + base64.b64encode(RAW).decode(),
    ],
)
def test_id_every_serialization(raw):
    assert m.parse_message_id(struct(message_id=raw)) == HEX


@pytest.mark.parametrize("key", ["0", 0, "messageID"])
def test_id_dict_keys(key):
    assert m.parse_message_id({key: RAW}) == HEX


def test_id_object_and_attribute_list():
    assert m.parse_message_id(struct(as_dict=False)) == HEX
    assert m.parse_message_id([struct()]) == HEX


@pytest.mark.parametrize(
    "raw",
    [
        None,
        b"1",
        "1",
        0x31,
        [0x31],
        "31",
        "MQ==",  # base64 of the legacy "1"
        b"",
        b"\x00" * 8,
        "0" * 16,
        RAW[:7],
        RAW + b"\x01",
        "zzzzzzzzzzzzzzzz",
        "not an id!",
        12345,
        3.5,
        object(),
        [1, 2, "x"],
        [256] * 8,
    ],
)
def test_id_none_for_legacy_or_garbage(raw):
    assert m.parse_message_id(struct(message_id=raw)) is None
    assert m.parse_message_id(raw) is None


def test_id_missing_and_never_raises():
    assert m.parse_message_id(struct(message_id=None)) is None
    assert m.parse_message_id(None) is None
    assert m.parse_message_id([]) is None
    assert m.parse_message_id({}) is None


def test_id_survives_json_round_trip():
    wire = json.loads(json.dumps({"0": list(RAW), "5": "x"}))
    assert m.parse_message_id(wire) == HEX


# --- parse_messages_full / value --------------------------------------------
def test_full_contract():
    assert m.parse_messages_full([struct()]) == ("Anna", "WhatsApp", "Hi", HEX)
    assert m.parse_messages_full([struct(message_id=b"1")]) == ("Anna", "WhatsApp", "Hi", None)
    assert m.parse_messages_full([]) == ("None", "None", "None", None)
    assert m.parse_messages_full("nope") is None
    assert m.parse_messages_full([{"0": RAW}]) is None  # no text


def test_value_keeps_original_contract():
    assert m.parse_messages_value([struct()]) == ("Anna", "WhatsApp", "Hi")
    assert m.parse_messages_value([]) == ("None", "None", "None")
    assert m.parse_messages_value(None) is None
    assert m.parse_messages_value([struct(text="a|b|c|d")]) == ("a", "b", "c|d")
    assert m.parse_messages_value([struct(text="plain")]) == ("Unknown", "Unknown", "plain")


# --- SeenIds ----------------------------------------------------------------
def test_seen_eviction_is_per_endpoint_and_oldest_first():
    seen = m.SeenIds(limit=3)
    for i in range(5):
        seen.add(1, f"id{i}")
    seen.add(2, "other")
    assert (1, "id0") not in seen and (1, "id1") not in seen
    assert all((1, f"id{i}") in seen for i in (2, 3, 4))
    assert (2, "other") in seen
    assert (2, "id4") not in seen


def test_seen_readd_refreshes_position():
    seen = m.SeenIds(limit=2)
    seen.add(1, "a")
    seen.add(1, "b")
    seen.add(1, "a")  # a is now the newest
    seen.add(1, "c")  # evicts b
    assert (1, "a") in seen and (1, "c") in seen and (1, "b") not in seen


def test_seen_persistence_round_trip():
    seen = m.SeenIds()
    seen.add(1, "aa")
    seen.add(3, "bb")
    stored = json.loads(json.dumps(seen.to_dict()))
    again = m.SeenIds.from_dict(stored)
    assert (1, "aa") in again and (3, "bb") in again and (2, "aa") not in again


@pytest.mark.parametrize("bad", [None, [], "x", {"x": ["a"]}, {"1": "a"}, {"1": [1, None, ""]}])
def test_seen_from_garbage(bad):
    seen = m.SeenIds.from_dict(bad)
    assert seen.to_dict() in ({}, {"1": []})


def test_seen_limit_applies_on_load():
    stored = {"1": [f"id{i}" for i in range(10)]}
    seen = m.SeenIds.from_dict(stored, limit=4)
    assert len(seen.to_dict()["1"]) == 4 and (1, "id9") in seen and (1, "id0") not in seen


# --- decide_event -----------------------------------------------------------
def msg(text_id=HEX, text=("Anna", "WhatsApp", "Hi")):
    return (*text, text_id)


def test_new_id_fires_once_then_replay_does_not():
    seen = m.SeenIds()
    assert m.decide_event(seen, 1, msg()) is True
    assert m.decide_event(seen, 1, msg()) is False  # replay after reconnect


def test_same_text_different_id_fires():
    seen = m.SeenIds()
    assert m.decide_event(seen, 1, msg("aa" * 8)) is True
    assert m.decide_event(seen, 1, msg("bb" * 8)) is True


def test_no_id_never_fires():
    seen = m.SeenIds()
    assert m.decide_event(seen, 1, msg(None)) is False
    assert seen.to_dict() == {}


def test_first_read_is_silent_but_remembered():
    seen = m.SeenIds()
    assert m.decide_event(seen, 1, msg(), silent=True) is False
    assert m.decide_event(seen, 1, msg()) is False  # now known: still no event
    assert m.decide_event(seen, 1, msg("cc" * 8)) is True


def test_disabled_endpoint_does_not_fire_or_remember():
    seen = m.SeenIds()
    assert m.decide_event(seen, 1, msg(), enabled=False) is False
    assert (1, HEX) not in seen
    assert m.decide_event(seen, 2, msg()) is True  # other endpoints unaffected


def test_clear_message_never_fires():
    seen = m.SeenIds()
    assert m.decide_event(seen, 1, ("None", "None", "None", HEX)) is False
    assert m.decide_event(seen, 1, ("None", "None", "None", None)) is False
    assert seen.to_dict() == {}


def test_same_id_on_two_endpoints_is_independent():
    seen = m.SeenIds()
    assert m.decide_event(seen, 1, msg()) is True
    assert m.decide_event(seen, 2, msg()) is True
