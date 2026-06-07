"""Trust-zone wrappers for model-visible external input.

XML tags here are a prompt-injection mitigation, not a security boundary. The
hard boundary remains schema validation, allowlists, and deterministic checks.
"""

from __future__ import annotations

import html
import json
from typing import Any


def escape_xml_text(text: str) -> str:
    """Escape text before placing it inside model-visible XML tags."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def wrap_user_request(text: str) -> str:
    return _wrap("external_user_request", text)


def wrap_assistant_history(text: str) -> str:
    return _wrap("previous_assistant_message", text)


def wrap_tool_result_json(tool: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    escaped = escape_xml_text(encoded)
    return (
        f'<trusted_tool_result tool="{escape_xml_text(tool)}">\n'
        f"<json>{escaped}</json>\n"
        "</trusted_tool_result>"
    )


def unwrap_tool_result_json(text: str) -> dict[str, Any] | None:
    start = text.find("<json>")
    end = text.rfind("</json>")
    if not text.startswith("<trusted_tool_result ") or start == -1 or end <= start:
        return None
    try:
        decoded = html.unescape(text[start + len("<json>"):end])
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _wrap(tag: str, text: str) -> str:
    return f"<{tag}>\n{escape_xml_text(text)}\n</{tag}>"
