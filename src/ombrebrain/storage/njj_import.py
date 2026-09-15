"""Offline NJJ summary initialization preview. Never calls a model or writes buckets."""
import argparse
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def quality_flags(text, date):
    flags = []
    day = str(date or "")[:10]
    if isinstance(date, (int, float)):
        try:
            day = datetime.fromtimestamp(date / 1000 if abs(date) > 1e11 else date, timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            day = ""
    if day == "1970-01-01":
        flags.append("invalid_epoch_date")
    # Sentence boundaries, not visual line wrapping; trailing quotes are not sentences.
    parts = re.split(r"[。！？!?]+|(?<!\d)\.(?!\d)", text)
    if sum(bool(re.search(r"[\w\u4e00-\u9fff]", part)) for part in parts) <= 1:
        flags.append("single_sentence")
    return flags


def preview(data, character_id):
    stores = data["data"]["structuredDB"]
    rows = []
    # Target ownership includes explicitly targeted group summaries, not arbitrary groups.
    for store in ("summaryEntries", "multiSceneSessionSummaries"):
        for r in stores.get(store, []):
            owners = [r.get("characterId"), r.get("targetCharacterId"), *r.get("cohortIds", [])]
            if character_id not in owners:
                continue
            text = r.get("summary") or r.get("content") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            source_id = str(r.get("id", ""))
            key = json.dumps(["njj", r.get("userId"), character_id, store, r.get("characterId"), source_id], ensure_ascii=False)
            rows.append({"key": key, "store": store, "source_id": source_id,
                         "owner": r.get("characterId"), "user_id": r.get("userId"),
                         "source": r.get("source", store), "date": r.get("date", r.get("timestamp")),
                         "text": text, "original_hash": fingerprint(text),
                         "is_big": bool(r.get("isBigSummary")),
                         "children": [str(i) for i in r.get("childSummaryIds", [])],
                         "parent": str(r.get("mergedIntoBigSummary") or ""),
                         "coverage": {k:r[k] for k in ("startMessageId", "endMessageId", "startMessageIndex", "lastMessageIndex", "messageCount", "dateRange", "sessionId", "groupId", "startIndex", "endIndex") if k in r},
                         "flags": ["disputed"] if r.get("disputed") else [], "selected": not bool(r.get("disputed"))})
    for row in rows:
        row["flags"].extend(quality_flags(row["text"], row["date"]))
        if "invalid_epoch_date" in row["flags"] or "single_sentence" in row["flags"]:
            row["selected"] = False
    for row in rows:
        parents = [p for p in rows if p["is_big"] and p["selected"]
                   and p["store"] == row["store"] and p["user_id"] == row["user_id"]
                   and p["owner"] == row["owner"] and p["source_id"] != row["source_id"]
                   and row["parent"] == p["source_id"] and row["source_id"] in p["children"]]
        if parents:
            row["flags"].append("covered_by_parent")
            row["selected"] = False
        elif row["parent"]:
            row["flags"].append("unverified_parent")
    seen = {}
    for row in rows:
        if row["selected"]:
            h = row["original_hash"]
            if h in seen:
                row["flags"].append("exact_text_duplicate")
                row["selected"] = False
            else:
                seen[h] = row["key"]
    return {"format": "njj-summary-review-v1", "character_id": character_id,
            "backup_timestamp": data.get("timestamp"), "entries": rows,
            "excluded_categories": ["character settings", "world books", "facts", "stance", "thinking", "raw dialogue", "media"]}


def write_review_html(result, out):
    payload = json.dumps(result, ensure_ascii=False).replace("<", "\\u003c")
    template = """<!doctype html><meta charset="utf-8"><title>njj 总结导入审核</title>
<style>body{font:16px system-ui;background:#f5f3ed;color:#282722;margin:0}header{position:sticky;top:0;background:#f5f3ed;padding:18px 5%;border-bottom:1px solid #ccc}main{max-width:1000px;margin:auto;padding:20px}article{background:white;padding:18px;margin:16px 0;border:1px solid #ddd}textarea{width:100%;box-sizing:border-box;min-height:140px;font:15px/1.7 system-ui;margin-top:12px}button,select,input{padding:8px;margin:4px}small{color:#666}h1{font-size:23px;margin:0 0 8px}</style>
<header><h1>njj 总结初始化 · 审核预览</h1><div>仅审核总结，不连接模型、不写入记忆库。修改后请导出审核清单保存。</div><select id="source"><option value="">全部来源</option></select><select id="mode"><option value="selected">已勾选</option><option value="all">全部候选</option><option value="flagged">有标记</option></select><input id="q" placeholder="搜索总结正文"><button id="save">导出审核清单</button><div id="count"></div></header><main id="list"></main>
<script id="data" type="application/json">PAYLOAD</script><script>
const data=JSON.parse(document.getElementById('data').textContent), rows=data.entries;
const source=document.getElementById('source'),mode=document.getElementById('mode'),q=document.getElementById('q');
for(const v of [...new Set(rows.map(r=>r.source))]){const o=document.createElement('option');o.value=v;o.textContent=v;source.append(o);}
const labels={invalid_epoch_date:'日期为 1970-01-01，默认排除',single_sentence:'整条仅一句话，默认排除',covered_by_parent:'已核对父子关联，默认采用大总结',unverified_parent:'父总结关联不完整，保留待审',disputed:'njj 标记为有争议',exact_text_duplicate:'正文完全重复'};
function count(){document.getElementById('count').textContent='共 '+rows.length+' 条候选 · 勾选 '+rows.filter(r=>r.selected).length+' 条';}
function render(){const box=document.getElementById('list');box.replaceChildren();count();for(const r of rows){if(source.value&&r.source!==source.value||mode.value==='selected'&&!r.selected||mode.value==='flagged'&&!r.flags.length||q.value&&!r.text.includes(q.value))continue;
const a=document.createElement('article'),label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=r.selected;check.onchange=()=>{r.selected=check.checked;count();};label.append(check,document.createTextNode(r.source+' · '+String(r.date||'日期未注明')+' · '+(r.is_big?'大总结':'事件总结')));a.append(label);
const info=document.createElement('p');info.textContent=r.flags.map(f=>labels[f]||f).join('；');a.append(info);const t=document.createElement('textarea');t.value=r.text;t.oninput=()=>{r.text=t.value;};a.append(t);const id=document.createElement('small');id.textContent='来源 ID：'+r.source_id;a.append(id);box.append(a);}}
source.onchange=mode.onchange=q.oninput=render;
document.getElementById('save').onclick=()=>{const missing=rows.filter(r=>!r.selected&&r.flags.includes('covered_by_parent')&&!rows.some(p=>p.selected&&p.source_id===r.parent&&p.owner===r.owner&&p.user_id===r.user_id));if(missing.length&&!confirm('有 '+missing.length+' 条子总结及其父总结均未勾选，确认排除这些内容？'))return;const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.href=url;a.download='njj-summary-reviewed.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};render();
</script>"""
    out.with_suffix(".html").write_text(template.replace("PAYLOAD", payload), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backup")
    parser.add_argument("--character", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with gzip.open(args.backup, "rt", encoding="utf-8") as f:
        data = json.load(f)
    result = preview(data, args.character)
    with open(args.backup, "rb") as source:
        result["backup_sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review_html(result, out)
    print(json.dumps({"candidates": len(result["entries"]), "selected": sum(r["selected"] for r in result["entries"]) }))


if __name__ == "__main__":
    main()
