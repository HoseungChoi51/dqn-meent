"""Exact, bounded views of stored research records.

An index is a navigation aid, not evidence that its referenced text was read.
Large text is returned under ``text_chunk`` so it cannot impersonate a complete
source passage in the citation validator.
"""
from __future__ import annotations

import json
import re

from optimization_framework.contracts.base import content_hash


RECORD_VIEW_LIMIT = 24 * 1024


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _type(value):
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    return "scalar"


def _child(pointer, key):
    return pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _select(value, pointer):
    if not pointer:
        return value
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("Use an RFC 6901 JSON pointer, starting with / and escaping ~ as ~0 and / as ~1")
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if key not in value:
                raise ValueError("The JSON pointer does not name a field in this record")
            value = value[key]
        elif isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("Array JSON pointers require a nonnegative integer index without leading zeros")
            # Bound conversion before int(), including Python's digit limit.
            if len(key) > len(str(len(value))) or int(key) >= len(value):
                raise ValueError("The JSON pointer array index is outside this record")
            value = value[int(key)]
        else:
            raise ValueError("The JSON pointer continues beyond a scalar value")
    return value


def view_record(value, *, record_id, pointer="", offset=0, limit=50,
                tool="evidence.read", max_bytes=RECORD_VIEW_LIMIT, envelope_bytes=0):
    """Select an exact subtree, or return an explicitly labelled bounded page.

    ``offset`` is a character offset for text and an entry offset for arrays or
    object indexes. ``limit`` bounds array/object page entries; text pages use
    the byte allowance. Small complete subtrees are returned unchanged.
    Callers must check campaign/reviewer access *before* invoking this helper.
    """
    if not isinstance(pointer, str):
        raise ValueError("A record pointer must be a string")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("A record page offset must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("A record page limit must be between 1 and 100 entries")
    allowance = max_bytes - envelope_bytes
    if allowance <= 0:
        raise ValueError("The record page needs a positive byte allowance")
    selected = _select(value, pointer)
    within_entries = not isinstance(selected, (dict, list)) or len(selected) <= limit
    if not offset and within_entries and _size(selected) <= allowance:
        return selected
    if not isinstance(selected, (dict, list, str)):
        raise ValueError("A scalar record value cannot be paged")
    if offset > len(selected):
        raise ValueError("The record page offset exceeds the selected value's length")

    def arguments(path=pointer, start=0):
        return {"record_id": record_id, "pointer": path, "offset": start, "limit": limit, "max_bytes": max_bytes}

    result = {"record_projection": "field_index" if isinstance(selected, dict) else
              "array_page" if isinstance(selected, list) else "text_page",
              "record_id": record_id, "record_hash": content_hash(value), "pointer": pointer,
              "value_type": _type(selected), "total": len(selected), "offset": offset,
              "next_offset": None, "read_tool": tool,
              "read_arguments": arguments(start=offset),
              "scope": "Only this page is supplied. Indexed fields are unread; use their exact pointer with read_tool. "
                       "Text chunks are not complete citable source passages. Compare record_hash across pages of a mutable record."}

    if isinstance(selected, str):
        # Budget serialized UTF-8, including escaping and navigation metadata.
        low, high = offset, len(selected)
        while low < high:
            end = (low + high + 1) // 2
            candidate = {**result, "text_chunk": selected[offset:end],
                         "next_offset": end if end < len(selected) else None}
            if _size(candidate) <= allowance:
                low = end
            else:
                high = end - 1
        result.update(text_chunk=selected[offset:low], next_offset=low if low < len(selected) else None)
        if low == offset and offset < len(selected) or _size(result) > allowance:
            raise ValueError("The record page metadata exceeds the byte allowance; use a shorter record pointer")
        return result

    field = "fields" if isinstance(selected, dict) else "items"
    result[field] = []
    keys = list(selected) if isinstance(selected, dict) else range(len(selected))
    for index in range(offset, min(len(selected), offset + limit)):
        key = keys[index]
        child = selected[key]
        child_pointer = _child(pointer, key)
        reference = {"pointer": child_pointer, "value_type": _type(child), "bytes": _size(child),
                     "read_arguments": arguments(child_pointer)}
        if isinstance(child, dict) and isinstance(child.get("id"), str):
            reference["record_id"] = child["id"]
        item = {"key": key, **reference} if isinstance(selected, dict) else child
        candidate = {**result, field: [*result[field], item],
                     "next_offset": index + 1 if index + 1 < len(selected) else None}
        if _size(candidate) > allowance and not isinstance(selected, dict):
            # One huge child must not make an array page impossible to read.
            item = {"record_projection": "reference", **reference}
            candidate[field] = [*result[field], item]
        if _size(candidate) > allowance:
            if not result[field]:
                raise ValueError("A record index entry exceeds the byte allowance; its field name or pointer is too large")
            break
        result = candidate
    if _size(result) > allowance:
        raise ValueError("The record page metadata exceeds the byte allowance")
    return result
