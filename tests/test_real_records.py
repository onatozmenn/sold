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
    if record.observation_model == "aggregate_observation":
        _assert_aggregate_matches(record)
    else:
        _assert_paired_matches(record)


def _assert_paired_matches(record):
    """Paired RealizedLabel kaydı: parser tam 1 etiket üretir ve alanlar eşleşir."""
    # Parser davranışı değiştiyse beklenen çıktı yeniden denetlenmeli.
    assert record.manifest.get("parser_version") == PARSER_VERSION, (
        f"{record.source}: manifest parser_version={record.manifest.get('parser_version')} "
        f"!= güncel {PARSER_VERSION} — beklenen çıktıyı yeniden denetle"
    )
    got = PublicLabelMiner().mine(record.source, [record.parser_input])
    assert len(got) == 1, f"{record.source}: parser tam 1 etiket üretmeli"
    out, exp = got[0], record.expected_output
    for key in record.compared_fields:
        got_val = out.get(key)
        exp_val = exp.get(key)
        if key == "transaction_date":  # parser date nesnesi → str kıyas
            got_val = str(got_val) if got_val is not None else None
        assert got_val == exp_val, (
            f"{record.source}.{key}: parser={got_val!r} != beklenen={exp_val!r}"
        )


def _assert_aggregate_matches(record):
    """Eşlenmemiş toplu gözlem kaydı: her popülasyon ayrı gözlem olarak eşleşmeli.

    Bu kayıt paired DEĞİLDİR (reference→realized ÇİFTİ yok); ``observation_role``'a
    göre eşlenmiş çoklu toplu gözlem beklenir. Kendi parser sürümüne (aggregate)
    pin'lenir.
    """
    from sold.labels.aggregates import (
        AGGREGATE_COMPARED_FIELDS,
        AGGREGATE_PARSER_VERSION,
        mine_aggregates,
    )

    assert record.manifest.get("parser_version") == AGGREGATE_PARSER_VERSION, (
        f"{record.source}: manifest parser_version={record.manifest.get('parser_version')} "
        f"!= güncel aggregate {AGGREGATE_PARSER_VERSION} — beklenen çıktıyı yeniden denetle"
    )
    got = mine_aggregates(record.source, [record.parser_input])
    exp = record.expected_output
    assert isinstance(exp, list), f"{record.source}: aggregate expected_output liste olmalı"
    assert len(got) == len(exp), (
        f"{record.source}: parser {len(got)} gözlem üretti, beklenen {len(exp)}"
    )
    got_by_role = {o["observation_role"]: o for o in got}
    exp_by_role = {o["observation_role"]: o for o in exp}
    assert set(got_by_role) == set(exp_by_role), (
        f"{record.source}: observation_role kümesi uyuşmuyor "
        f"({set(got_by_role)} != {set(exp_by_role)})"
    )
    for role, e in exp_by_role.items():
        g = got_by_role[role]
        for key in AGGREGATE_COMPARED_FIELDS:
            got_val = g.get(key)
            exp_val = e.get(key)
            if key == "as_of_date":  # parser date nesnesi → str kıyas
                got_val = str(got_val) if got_val is not None else None
            assert got_val == exp_val, (
                f"{record.source}[{role}].{key}: parser={got_val!r} != beklenen={exp_val!r}"
            )
