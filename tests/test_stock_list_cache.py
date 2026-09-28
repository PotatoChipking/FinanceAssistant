"""股票清单刷新失败时保留完整的 A 股扫描范围。"""

from __future__ import annotations

import json
import time

from src.web import stock_list


def _cache(monkeypatch, tmp_path, stocks: list[dict]) -> tuple[object, str]:
    path = tmp_path / "stock_list_cache.json"
    original = json.dumps(
        {"ts": time.time() - stock_list.CACHE_TTL - 60, "stocks": stocks},
        ensure_ascii=False,
    )
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(stock_list, "CACHE_FILE", str(path))
    return path, original


def _fail() -> list[dict]:
    raise RuntimeError("offline")


def test_expired_cache_survives_complete_refresh_failure(monkeypatch, tmp_path, caplog):
    cached = [
        {"symbol": "600000", "name": "浦发银行", "market": "CN"},
        {"symbol": "00700", "name": "腾讯控股", "market": "HK"},
    ]
    path, original = _cache(monkeypatch, tmp_path, cached)
    monkeypatch.setattr(stock_list, "_fetch_from_eastmoney", _fail)
    monkeypatch.setattr(stock_list, "_fetch_from_akshare", _fail)
    monkeypatch.setattr(stock_list, "_fetch_hk_from_eastmoney", _fail)
    monkeypatch.setattr(stock_list, "_fetch_us_from_eastmoney", _fail)
    monkeypatch.setattr(stock_list, "_fetch_bj_from_eastmoney", _fail)

    assert stock_list.get_stock_list() == cached
    assert path.read_text(encoding="utf-8") == original
    assert "沿用过期缓存" in caplog.text


def test_partial_refresh_keeps_cached_cn_without_overwriting_cache(monkeypatch, tmp_path, caplog):
    cached = [
        {"symbol": "600000", "name": "浦发银行", "market": "CN"},
        {"symbol": "00700", "name": "腾讯控股", "market": "HK"},
    ]
    path, original = _cache(monkeypatch, tmp_path, cached)
    monkeypatch.setattr(stock_list, "_fetch_from_eastmoney", _fail)
    monkeypatch.setattr(stock_list, "_fetch_from_akshare", _fail)
    monkeypatch.setattr(
        stock_list,
        "_fetch_hk_from_eastmoney",
        lambda: [{"symbol": "09988", "name": "阿里巴巴", "market": "HK"}],
    )
    monkeypatch.setattr(stock_list, "_fetch_us_from_eastmoney", lambda: [])
    monkeypatch.setattr(stock_list, "_fetch_bj_from_eastmoney", lambda: [])

    refreshed = stock_list.get_stock_list()

    assert {(s["market"], s["symbol"]) for s in refreshed} == {
        ("CN", "600000"),
        ("HK", "09988"),
    }
    assert path.read_text(encoding="utf-8") == original
    assert "A 股股票列表刷新失败" in caplog.text
