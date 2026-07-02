from __future__ import annotations

import json


ALLOWED_ACTIONS = {
    "buy",
    "add",
    "reduce",
    "sell",
    "hold",
    "watch",
    "alert",
    "avoid",
}

ACTION_ALIASES = {
    "build": "add",
}


TAG_START = "<!--PANWATCH_JSON-->"
TAG_END = "<!--/PANWATCH_JSON-->"


def try_parse_action_json(text: str) -> dict | None:
    """Parse JSON-only output. Returns dict on success."""
    raw = (text or "").strip()
    if not raw:
        return None

    # Allow fenced code blocks (```json ... ```)
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[0].lstrip().startswith("```"):
            if lines[-1].strip().startswith("```"):
                raw = "\n".join(lines[1:-1]).strip()
        else:
            raw = raw.strip("`").strip()
    # Allow "json" prefix line without code fences.
    # Example:
    # json
    # {"action":"buy", ...}
    lines = raw.splitlines()
    if lines and lines[0].strip().lower() == "json":
        raw = "\n".join(lines[1:]).strip()
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    action = (obj.get("action") or "").strip().lower()
    if action in ACTION_ALIASES:
        obj["action"] = ACTION_ALIASES[action]
        action = obj["action"]
    if action and action not in ALLOWED_ACTIONS:
        return None
    return obj


def try_extract_tagged_json(
    text: str, *, start: str = TAG_START, end: str = TAG_END
) -> dict | None:
    """Extract a tagged JSON object from a larger text.

    Expected format at the end of the response:
    <!--PANWATCH_JSON-->
    { ... }
    <!--/PANWATCH_JSON-->
    """

    raw = text or ""
    i = raw.rfind(start)
    if i < 0:
        return None
    j = raw.rfind(end)
    if j < 0 or j <= i:
        return None
    payload = raw[i + len(start) : j].strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


# 结论文案 → breakout 枚举。按特异性从高到低匹配，避免「有效突破」被「突破」误判。
_BREAKOUT_VERDICT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("failed", ("突破失败", "假突破", "突破无效", "突破未成立")),
    ("expired", ("突破已过期", "突破过期", "突破失效")),
    ("pending", ("突破待确认", "待确认", "有待确认", "尚未确认", "暂未确认")),
    ("valid", ("有效突破", "突破有效", "突破已确认", "确认有效", "有效性成立")),
    ("none", ("不符合", "无突破", "未突破", "未发生突破", "不构成突破")),
]


def infer_breakout_from_text(text: str) -> str | None:
    """从结论/正文里推断 breakout 状态枚举，判断不出则返回 None。"""
    raw = text or ""
    for value, keywords in _BREAKOUT_VERDICT_PATTERNS:
        if any(kw in raw for kw in keywords):
            return value
    return None


def reconcile_breakout_tag(verdict_or_prose: str, tags: dict | None) -> dict | None:
    """用 AI 的文字结论校正 breakout 标签，二者矛盾时以文字结论为准。

    标签(结构化 JSON)与正文结论来自同一次回复，但模型偶尔会写「有效突破」却在
    JSON 里给 pending。这里以用户实际读到的文字结论为准，保证徽章与结论一致。
    """
    if not isinstance(tags, dict):
        return tags
    inferred = infer_breakout_from_text(verdict_or_prose)
    if inferred and tags.get("breakout") != inferred:
        tags = dict(tags)
        tags["breakout"] = inferred
    return tags


def strip_tagged_json(text: str, *, start: str = TAG_START, end: str = TAG_END) -> str:
    """Remove tagged JSON block from text (if present)."""
    raw = text or ""
    i = raw.rfind(start)
    if i < 0:
        return raw
    j = raw.rfind(end)
    if j < 0 or j <= i:
        return raw
    return (raw[:i] + raw[j + len(end) :]).strip()
