"""Feishu message builders: markdown text messages + report cards + chunking.

Feishu interactive cards render `lark_md` (a markdown subset). Long reports are
split into multiple cards at section boundaries to stay under size limits.
"""

from __future__ import annotations

import json

# Feishu card content limit is ~30KB; stay well under it for safety.
MAX_CARD_CHARS = 3500

_CARD_TEMPLATE = {
    "config": {"wide_screen_mode": True},
    "header": {
        "title": {"tag": "plain_text", "content": ""},
        "template": "blue",
    },
    "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": ""}}
    ],
}


def text_message(text: str) -> dict:
    """Plain text message content (msg_type=text)."""
    return {"msg_type": "text", "content": json.dumps({"text": text})}


def markdown_card(title: str, body_md: str) -> dict:
    """Interactive card content (msg_type=interactive) rendering markdown."""
    card = json.loads(json.dumps(_CARD_TEMPLATE))
    card["header"]["title"]["content"] = title[:60] or "選情分析"
    card["elements"] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body_md}}
    ]
    return {"msg_type": "interactive", "content": json.dumps(card)}


def split_markdown(md: str, limit: int = MAX_CARD_CHARS) -> list[str]:
    """Split a markdown document into chunks at heading/paragraph boundaries.

    Never splits inside a line; falls back to hard cuts only when a single
    line exceeds the limit.
    """
    if len(md) <= limit:
        return [md]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in md.split("\n"):
        is_boundary = line.startswith("#") or not line.strip()
        if size + len(line) + 1 > limit and current and is_boundary:
            chunks.append("\n".join(current))
            current, size = [], 0
        elif size + len(line) + 1 > limit and current:
            # mid-paragraph overflow: still cut, but keep the line intact
            chunks.append("\n".join(current))
            current, size = [], 0
        if len(line) > limit:  # pathological single line
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            continue
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]


def report_messages(title: str, body_md: str) -> list[dict]:
    """A report as one or more card messages, split at section boundaries."""
    parts = split_markdown(body_md)
    messages = []
    for i, part in enumerate(parts):
        suffix = f"（{i + 1}/{len(parts)}）" if len(parts) > 1 else ""
        messages.append(markdown_card(f"{title}{suffix}", part))
    return messages or [text_message("（報告內容為空）")]


def report_summary(body_md: str, limit: int = MAX_CARD_CHARS) -> str:
    """Card-sized digest of a report: overall verdict + viewpoint titles."""
    out: list[str] = []
    for raw in body_md.splitlines():
        line = raw.strip()
        if "研判：" in line and not out:
            out.append(line if line.startswith("**") else f"**{line}**")
        elif line.startswith("### "):
            out.append(f"\n**{line[4:].strip()}**")
        elif line.startswith("**觀點") and line.endswith("**"):
            out.append("・" + line.strip("*").strip())
    # drop party/section headings that ended up with no viewpoints under them
    out = [ln for i, ln in enumerate(out)
           if not (ln.startswith("\n**") and
                   (i + 1 == len(out) or out[i + 1].startswith("\n**")))]
    text = "\n".join(out).strip()
    if not text:
        text = body_md[:600]
    return text[:limit]
