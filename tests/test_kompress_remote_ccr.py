"""Regression tests for RemoteKompressCompressor's CCR handling.

Guards two bugs found together:

1. Signature drift — RemoteKompressCompressor.compress() must accept every param the
   local KompressCompressor.compress() does, because ContentRouter calls them
   interchangeably. It once lacked ``ccr_original``, so the router's call raised
   TypeError, was caught, and the content was silently skipped (0% compression on
   protected-tag blocks).

2. Wrong CCR original — when custom tags are protected the router passes the
   placeholdered text as ``content`` and the real text as ``ccr_original``. The remote
   path must store ``ccr_original`` (fallback ``content``), else a later /v1/retrieve
   returns ``{{HEADROOM_TAG_N}}`` and the protected block is lost.
"""

from __future__ import annotations

import inspect

import headroom.transforms.kompress_remote as kr
from headroom.transforms.kompress_compressor import KompressCompressor, KompressConfig
from headroom.transforms.kompress_remote import RemoteKompressCompressor


class _StubResp:
    def raise_for_status(self) -> None: ...

    def json(self) -> dict:
        # Short "compressed" -> ratio well under _CCR_RATIO_GATE so the CCR branch runs.
        return {
            "compressed": "short summary",
            "original_tokens": 500,
            "compressed_tokens": 2,
            "compression_ratio": 0.1,
        }


def _compressor(monkeypatch, captured: dict) -> RemoteKompressCompressor:
    monkeypatch.setattr(
        kr,
        "store_kompress_in_ccr",
        lambda original, compressed, toks: captured.__setitem__("original", original) or "hashXYZ",
    )
    c = RemoteKompressCompressor(endpoint="http://stub", config=KompressConfig(enable_ccr=True))
    monkeypatch.setattr(c._client, "post", lambda *a, **k: _StubResp())
    return c


def test_remote_accepts_every_local_compress_param() -> None:
    local = set(inspect.signature(KompressCompressor.compress).parameters)
    remote = set(inspect.signature(RemoteKompressCompressor.compress).parameters)
    assert not (local - remote), f"remote drop-in missing params: {local - remote}"


def test_ccr_original_stored_not_placeholder(monkeypatch) -> None:
    captured: dict = {}
    c = _compressor(monkeypatch, captured)
    placeholder = "{{HEADROOM_TAG_0}} " * 60  # what the router sends as `content` when protected
    real_text = "the real pre-protection configuration block " * 20
    c.compress(placeholder, ccr_original=real_text)
    assert "real pre-protection" in captured["original"]
    assert "HEADROOM_TAG" not in captured["original"]


def test_ccr_falls_back_to_content_when_no_original(monkeypatch) -> None:
    captured: dict = {}
    c = _compressor(monkeypatch, captured)
    c.compress("plain content here " * 60)
    assert "plain content here" in captured["original"]
