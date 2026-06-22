"""腾讯板块排行采集器。

数据源:proxy.finance.qq.com 的 getRank 接口(行业 hy / 概念 gn),
作为东方财富 push2 板块排行不可用时的替代/主源。

字段名按腾讯返回做了多重兜底(zdf/zd/cje 等),解析失败返回空列表,
由上层回退到东方财富/合成板块,保证不比现状更差。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _num(item: dict, keys: list[str]) -> float | None:
    for key in keys:
        if key in item and item[key] not in (None, "", "-"):
            try:
                return float(item[key])
            except (TypeError, ValueError):
                continue
    return None


def _text(item: dict, keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", "-"):
            return str(value).strip()
    return ""


class TencentBoardCollector:
    """腾讯行业/概念板块排行。"""

    RANK_API = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"

    def __init__(
        self,
        *,
        timeout_s: float = 12.0,
        proxy: str | None = None,
        retries: int = 1,
        backoff_s: float = 0.4,
    ):
        self.timeout_s = float(timeout_s)
        self.proxy = proxy
        self.retries = int(retries)
        self.backoff_s = float(backoff_s)

    @staticmethod
    def _extract_rows(data: Any) -> list[dict]:
        """从多种可能的结构里取出板块列表。"""
        payload = (data or {}).get("data") if isinstance(data, dict) else None
        if isinstance(payload, dict):
            for key in ("rank_list", "list", "rank", "data", "ranklist"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return rows
        if isinstance(payload, list):
            return payload
        return []

    async def fetch_hot_boards(self, *, scope: str = "industry", limit: int = 20) -> list[dict]:
        board_type = "gn" if (scope or "").strip().lower() in ("concept", "concepts", "gn") else "hy"
        # getRank 没有「涨跌幅」排序枚举,这里按成交额取活跃板块池,涨幅交由上层客户端排序。
        params = {
            "board_type": board_type,
            "sort_type": "turnover",
            "direct": "down",
            "offset": 0,
            "count": max(1, min(int(limit), 100)),
        }
        data = await self._get_json(params)
        rows = self._extract_rows(data)
        out: list[dict] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            code = _text(item, ["code", "symbol", "id"])
            name = _text(item, ["name", "board_name", "title"])
            if not code or not name:
                continue
            # turnover 单位为万元,统一换算成元,与东方财富/前端 formatAmount 保持一致。
            turnover_wan = _num(item, ["turnover", "cje", "amount", "money"])
            turnover = turnover_wan * 10000.0 if turnover_wan is not None else None
            lzg = item.get("lzg") if isinstance(item.get("lzg"), dict) else {}
            out.append(
                {
                    "code": code,
                    "name": name,
                    "change_pct": _num(item, ["zdf", "change_pct", "pchg"]),
                    "change_amount": _num(item, ["zd", "change"]),
                    "turnover": turnover,
                    "scope": "concept" if board_type == "gn" else "industry",
                    "leader_name": _text(lzg, ["name"]) or None,
                    "leader_code": _text(lzg, ["code"]) or None,
                    "leader_change_pct": _num(lzg, ["zdf"]),
                }
            )
        return out

    async def _get_json(self, params: dict) -> dict:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://gu.qq.com/",
        }
        attempts = max(self.retries, 0) + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                timeout = httpx.Timeout(self.timeout_s, connect=min(self.timeout_s, 6.0))
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    trust_env=True,
                    headers=headers,
                    proxy=self.proxy,
                ) as client:
                    resp = await client.get(self.RANK_API, params=params)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as exc:  # noqa: BLE001 - 上层据空结果回退
                last_exc = exc
                if attempt < attempts - 1:
                    import asyncio

                    await asyncio.sleep(self.backoff_s * (attempt + 1))
        logger.warning("Tencent board rank request failed: %s: %s", type(last_exc).__name__, last_exc)
        return {}


if __name__ == "__main__":  # 本地验证:python -m src.collectors.tencent_board_collector
    import asyncio
    import json

    async def _main() -> None:
        collector = TencentBoardCollector()
        for scope in ("industry", "concept"):
            boards = await collector.fetch_hot_boards(scope=scope, limit=10)
            print(f"\n=== {scope}: {len(boards)} 个 ===")
            print(json.dumps(boards[:5], ensure_ascii=False, indent=2))

    asyncio.run(_main())
