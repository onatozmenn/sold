"""Gerçek-kayıt (level-2) doğrulama testleri — illüstratif fixture'lardan (level-1) AYRI.

Slotlar PENDING iken ATLANIR (operatör gerçek resmî kaydı indirip elle denetlemeli).
Gerçek + denetlenmiş olduğunda parser çıktısı beklenen çıktıya EŞİT olmalı; parser
sürümü değiştiyse yeniden denetim istenir.
"""

from __future__ import annotations

import pytest

from sold.labels import PublicLabelMiner
from sold.labels.registry import PARSER_VERSION
from sold.labels.validation import (
    COMPARED_FIELDS,
    MANIFEST_FIELDS,
    load_validation_records,
)

_RECORDS = load_validation_records()


def test_three_source_slots_present_and_wellformed():
    by_source = {r.source: r for r in _RECORDS}
    assert {"kap", "uyap", "toki"}.issubset(by_source), "KAP/UYAP/TOKİ slotları bulunmalı"
    miner_sources = set(PublicLabelMiner().sources())
    for r in _RECORDS:
        for field in MANIFEST_FIELDS:
            assert field in r.manifest, f"{r.path.name}: manifest '{field}' alanı eksik"
        assert r.manifest["source"] in miner_sources
        assert r.manifest["source"] == r.path.stem


@pytest.mark.parametrize("record", _RECORDS, ids=[r.source for r in _RECORDS] or None)
def test_real_record_parser_matches_audited_expected(record):
    if not record.ready:
        pytest.skip(
            f"{record.source}: gerçek kayıt PENDING — operatör indirip elle denetlemeli "
            "(is_real_official_record + manually_audited = true)"
        )
    # Parser davranışı değiştiyse beklenen çıktı yeniden denetlenmeli.
    assert record.manifest.get("parser_version") == PARSER_VERSION, (
        f"{record.source}: manifest parser_version={record.manifest.get('parser_version')} "
        f"!= güncel {PARSER_VERSION} — beklenen çıktıyı yeniden denetle"
    )
    got = PublicLabelMiner().mine(record.source, [record.parser_input])
    assert len(got) == 1, f"{record.source}: parser tam 1 etiket üretmeli"
    out, exp = got[0], record.expected_output
    for key in COMPARED_FIELDS:
        got_val = out.get(key)
        exp_val = exp.get(key)
        if key == "transaction_date":  # parser date nesnesi → str kıyas
            got_val = str(got_val) if got_val is not None else None
        assert got_val == exp_val, (
            f"{record.source}.{key}: parser={got_val!r} != beklenen={exp_val!r}"
        )
