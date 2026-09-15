"""Apply an explicitly reviewed NJJ summary list to an offline vault."""
import asyncio
import gzip
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
import frontmatter
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bucket_manager import BucketManager

async def apply(review_path, backup_path, vault):
    review=json.loads(Path(review_path).read_text(encoding="utf8"))
    with open(backup_path,"rb") as f:
        if hashlib.file_digest(f,"sha256").hexdigest()!=review["backup_sha256"]:
            raise ValueError("Backup fingerprint mismatch")
    with gzip.open(backup_path,"rt",encoding="utf8") as f:
        stores=json.load(f)["data"]["structuredDB"]
    manager=BucketManager({"buckets_dir":str(vault)})
    existing={}
    for p in vault.rglob("*.md"):
        post=frontmatter.load(p)
        if post.get("njj_source_key"):existing[post["njj_source_key"]]=post
    records=[]
    for row in review["entries"]:
        if not row["selected"]:continue
        key=row["key"]
        digest=hashlib.sha256(row["text"].encode()).hexdigest()
        if key in existing:
            if existing[key].get("njj_import_hash")!=digest:raise ValueError("Existing source changed; review required")
            records.append({"key":key,"id":existing[key]["id"],"status":"already_imported"});continue
        originals=[r for r in stores[row["store"]] if str(r.get("id",""))==row["source_id"] and r.get("userId")==row["user_id"] and r.get("characterId")==row["owner"]]
        if len(originals)!=1:raise ValueError("Ambiguous source")
        original=originals[0]
        title=row["text"].strip().split("。",1)[0][:40]
        tags=original.get("keywords",[]);topics=original.get("topics",[])
        tags=[x for x in tags if isinstance(x,str)] if isinstance(tags,list) else []
        topics=[x for x in topics if isinstance(x,str)] if isinstance(topics,list) else []
        importance=original.get("importance",5)
        importance=max(1,min(10,int(importance))) if isinstance(importance,(int,float)) else 5
        bid=await manager.create(row["text"],tags=tags,domain=topics,importance=importance,title=title,imported=True,source_tool="import",grow_batch_id="njj-init-"+review["backup_sha256"][:12],bucket_id_override="njj_"+hashlib.sha256(key.encode()).hexdigest()[:20],defer_derived_index=True)
        path=Path(manager._find_bucket_file(bid));post=frontmatter.load(path)
        post["njj_source_key"]=key;post["njj_import_hash"]=digest
        post["njj_original_hash"]=row["original_hash"]
        post["njj_event_date"]=row["date"];post["njj_coverage"]=row["coverage"]
        post["njj_children"]=row["children"];post["njj_backup_sha256"]=review["backup_sha256"]
        post["njj_character_id"]=review["character_id"]
        fd,tmp=tempfile.mkstemp(dir=path.parent,suffix=".tmp")
        with os.fdopen(fd,"w",encoding="utf8") as f:f.write(frontmatter.dumps(post))
        os.replace(tmp,path)
        records.append({"key":key,"id":bid,"status":"created"})
    manifest=Path(review_path).with_name("applied-manifest.json")
    manifest.write_text(json.dumps({"vault":str(vault),"records":records},ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps({"count":len(records),"created":sum(r["status"]=="created" for r in records)}))

if __name__=="__main__":
    asyncio.run(apply(sys.argv[1],sys.argv[2],Path(sys.argv[3]).resolve()))
