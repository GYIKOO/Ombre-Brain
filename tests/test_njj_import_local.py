from ombrebrain.storage.njj_import import preview


def test_only_summaries_with_verified_parent_links():
    rows = [{"id":1,"characterId":"c","summary":"child. Second sentence.","mergedIntoBigSummary":2},
            {"id":2,"characterId":"c","summary":"parent. Second sentence.","isBigSummary":True,"childSummaryIds":[1]},
            {"id":3,"characterId":"c","summary":"orphan. Second sentence.","mergedIntoBigSummary":99},
            {"id":4,"characterId":"other","summary":"not ours. Second sentence."},
            {"id":5,"characterId":"c","summary":"disputed. Second sentence.","disputed":True}]
    d={"data":{"structuredDB":{"summaryEntries":rows,"memory":[{"userFacts":["no"]}]}}}
    result=preview(d,"c")["entries"]
    assert len(result)==4
    assert [r["source_id"] for r in result if r["selected"]]==["2","3"]
    assert "unverified_parent" in result[2]["flags"]
    assert preview(d,"c")==preview(d,"c")


def test_quality_filters():
    from ombrebrain.storage.njj_import import quality_flags
    assert "invalid_epoch_date" in quality_flags("第一句。第二句。", "1970-01-01T00:00:00Z")
    assert "invalid_epoch_date" in quality_flags("第一句。第二句。", 0)
    assert "single_sentence" in quality_flags("只有一句，虽有逗号。", "2026-09-14")
    assert "single_sentence" in quality_flags("他说：“好！”", "2026-09-14")
    assert quality_flags("第一句。第二句。", "2026-09-14") == []
