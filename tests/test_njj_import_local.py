from ombrebrain.storage.njj_import import preview


def test_only_summaries_with_verified_parent_links():
    rows = [{"id":1,"characterId":"c","summary":"child","mergedIntoBigSummary":2},
            {"id":2,"characterId":"c","summary":"parent","isBigSummary":True,"childSummaryIds":[1]},
            {"id":3,"characterId":"c","summary":"orphan","mergedIntoBigSummary":99},
            {"id":4,"characterId":"other","summary":"not ours"},
            {"id":5,"characterId":"c","summary":"disputed","disputed":True}]
    d={"data":{"structuredDB":{"summaryEntries":rows,"memory":[{"userFacts":["no"]}]}}}
    result=preview(d,"c")["entries"]
    assert len(result)==4
    assert [r["source_id"] for r in result if r["selected"]]==["2","3"]
    assert "unverified_parent" in result[2]["flags"]
    assert preview(d,"c")==preview(d,"c")
