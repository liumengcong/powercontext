from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass


class ContextPackError(ValueError):
    """Raised when a context pack or delta is malformed or inconsistent."""


_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _decode_digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise ContextPackError("invalid digest")
    try:
        int(value[7:], 16)
    except ValueError as error:
        raise ContextPackError("invalid digest") from error
    if value != value.lower():
        raise ContextPackError("invalid digest")
    return value


def _wire_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _parse_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContextPackError("items must be an array of base64 strings")
    try:
        decoded = tuple(base64.b64decode(item, validate=True) for item in value)
    except (ValueError, base64.binascii.Error) as error:
        raise ContextPackError("invalid base64 item") from error
    if _wire_items(decoded) != value:
        raise ContextPackError("non-canonical base64 item")
    return decoded


@dataclass(frozen=True, slots=True)
class ContextPack:
    digest: str
    items: tuple[bytes, ...]
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...] | list[bytes]) -> ContextPack:
        normalized = tuple(items)
        if any(not isinstance(item, bytes) for item in normalized):
            raise ContextPackError("items must be bytes")
        unsigned = {"schema": _PACK_SCHEMA, "items": _wire_items(normalized)}
        digest = _digest(unsigned)
        payload = {**unsigned, "digest": digest, "byte_count": 0}
        count = len(_canonical(payload))
        while True:
            payload["byte_count"] = count
            encoded = _canonical(payload)
            if len(encoded) == count:
                return cls(digest, normalized, count)
            count = len(encoded)

    @classmethod
    def from_content(cls, content: str, chunk_bytes: int = 4096) -> ContextPack:
        if not isinstance(content, str) or type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("invalid content or chunk size")
        raw = content.encode("utf-8")
        chunks: list[bytes] = []
        offset = 0
        while offset < len(raw):
            end = min(offset + chunk_bytes, len(raw))
            while end < len(raw) and (raw[end] & 0xC0) == 0x80:
                end -= 1
            if end == offset:
                raise ContextPackError("chunk size is smaller than a UTF-8 character")
            chunks.append(raw[offset:end])
            offset = end
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        unsigned = {"schema": _PACK_SCHEMA, "items": _wire_items(self.items)}
        if _digest(unsigned) != self.digest:
            raise ContextPackError("pack digest mismatch")
        payload = {**unsigned, "digest": self.digest}
        encoded = _canonical({**payload, "byte_count": len(_canonical(payload))})
        # byte_count describes the complete serialized payload, including itself.
        count = len(encoded)
        while True:
            payload["byte_count"] = count
            encoded = _canonical(payload)
            if len(encoded) == count:
                return encoded
            count = len(encoded)

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError) as error:
            raise ContextPackError("invalid pack JSON") from error
        if not isinstance(payload, dict) or set(payload) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("invalid pack fields")
        if payload["schema"] != _PACK_SCHEMA or type(payload["byte_count"]) is not int:
            raise ContextPackError("invalid pack header")
        items = _parse_items(payload["items"])
        digest = _decode_digest(payload["digest"])
        unsigned = {"schema": _PACK_SCHEMA, "items": _wire_items(items)}
        if _digest(unsigned) != digest or _canonical(payload) != raw or payload["byte_count"] != len(raw):
            raise ContextPackError("pack integrity check failed")
        return cls(digest, items, len(raw))


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        for left, right in zip(base.items, target.items):
            if left != right:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    def to_bytes(self) -> bytes:
        _decode_digest(self.base_digest)
        _decode_digest(self.target_digest)
        if type(self.prefix_items) is not int or self.prefix_items < 0 or any(not isinstance(x, bytes) for x in self.appended):
            raise ContextPackError("invalid delta")
        return _canonical({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _wire_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError) as error:
            raise ContextPackError("invalid delta JSON") from error
        fields = {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        if not isinstance(payload, dict) or set(payload) != fields or payload["schema"] != _DELTA_SCHEMA:
            raise ContextPackError("invalid delta fields")
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("invalid delta prefix")
        base_digest = _decode_digest(payload["base_digest"])
        target_digest = _decode_digest(payload["target_digest"])
        appended = _parse_items(payload["appended"])
        delta = cls(base_digest, target_digest, prefix, appended)
        return delta


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if base.digest != delta.base_digest or type(delta.prefix_items) is not int:
        raise ContextPackError("delta base mismatch")
    if not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("delta prefix out of bounds")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target mismatch")
    return target
