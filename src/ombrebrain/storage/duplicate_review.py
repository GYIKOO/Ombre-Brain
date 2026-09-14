"""Local-vector duplicate candidates and reversible human archive decisions."""

import asyncio
import weakref
import hashlib
import math
import sqlite3
import uuid
from contextlib import contextmanager

from .ingest_archive import archive_directory


def content_hash(bucket):
    return hashlib.sha256(str(bucket.get("content", "")).encode("utf-8")).hexdigest()


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    norm = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    if not norm:
        return 0.0
    value = sum(x * y for x, y in zip(a, b)) / norm
    return value if math.isfinite(value) else 0.0


@contextmanager
def database():
    path = archive_directory().parent / "duplicate-review.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, a TEXT, b TEXT, ah TEXT, bh TEXT, state TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    try:
        with db:
            yield db
    finally:
        db.close()


async def candidates(buckets, engine, threshold=0.80):
    vectors = {}
    for bucket in buckets:
        if engine and getattr(engine, "enabled", False):
            vectors[bucket["id"]] = await engine.get_embedding(bucket["id"])
    with database() as db:
        dismissed = {
            (a, b, ah, bh) for a, b, ah, bh in db.execute("SELECT a,b,ah,bh FROM reviews WHERE state IN ('dismissed','undone')")
        }
    pairs = []
    for index, a in enumerate(buckets):
        for b in buckets[index + 1 :]:
            ah, bh = content_hash(a), content_hash(b)
            if (a["id"], b["id"], ah, bh) in dismissed or (b["id"], a["id"], bh, ah) in dismissed:
                continue
            exact = ah == bh and bool(a.get("content", "").strip())
            score = 1.0 if exact else cosine(vectors.get(a["id"]), vectors.get(b["id"]))
            if score < threshold:
                continue

            def display(bucket, h):
                meta = bucket.get("metadata", {})
                return {"id": bucket["id"], "name": meta.get("title") or meta.get("name", bucket["id"]), "hash": h}

            pairs.append({"a": display(a, ah), "b": display(b, bh), "score": score, "exact": exact})
    return sorted(pairs, key=lambda p: p["score"], reverse=True)


async def _review(manager, action, a, b, ah, bh):
    if a == b:
        raise ValueError("请选择两条不同记忆")
    left, right = await manager.get(a), await manager.get(b)
    if not left or not right or content_hash(left) != ah or content_hash(right) != bh:
        raise ValueError("记忆已变化，请刷新后重新核对")
    for bucket in (left, right):
        meta = bucket.get("metadata", {})
        if meta.get("type") != "dynamic" or meta.get("protected") or meta.get("pinned"):
            raise ValueError("第一版只处理未保护、未钉选的普通动态记忆")
    if action not in ("archive", "dismiss"):
        raise ValueError("未知操作")
    rid = uuid.uuid4().hex
    with database() as db:
        db.execute(
            "INSERT INTO reviews(id,a,b,ah,bh,state) VALUES(?,?,?,?,?,?)",
            (rid, a, b, ah, bh, "pending" if action == "archive" else "dismissed"),
        )
    if action == "archive":
        if not await manager.archive(b):
            raise ValueError("归档失败，原文保留")
        with database() as db:
            db.execute("UPDATE reviews SET state='archived' WHERE id=?", (rid,))
    return rid


def history():
    with database() as db:
        return [
            {"id": r[0], "representative": r[1], "archived": r[2], "state": r[3]}
            for r in db.execute(
                "SELECT id,a,b,state FROM reviews WHERE state IN ('archived','pending') ORDER BY created DESC"
            )
        ]


async def _undo(manager, rid):
    with database() as db:
        row = db.execute("SELECT b,bh,state FROM reviews WHERE id=?", (rid,)).fetchone()
    if not row or row[2] not in ("archived", "pending"):
        raise ValueError("没有可撤销的归档")
    bucket = await manager.get_including_archive(row[0])
    if not bucket or content_hash(bucket) != row[1] or bucket.get("metadata", {}).get("type") != "archived":
        raise ValueError("归档条目已变化，请手动核对")
    result = await manager.restore_archived(row[0])
    if not result.get("ok"):
        raise ValueError("恢复失败")
    with database() as db:
        db.execute("UPDATE reviews SET state='undone' WHERE id=?", (rid,))


_review_locks = weakref.WeakKeyDictionary()


async def review(manager, action, a, b, ah, bh):
    lock = _review_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with lock:
        return await _review(manager, action, a, b, ah, bh)


async def undo(manager, rid):
    lock = _review_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with lock:
        return await _undo(manager, rid)


async def scan(manager, engine, buckets, review_threshold=0.80, auto_threshold=0.985):
    """Two bands; archive direct pairs only, never infer transitive duplicates."""
    if not (0 <= review_threshold < auto_threshold <= 1):
        raise ValueError("阈值须满足 0 ≤ 人工阈值 < 自动阈值 ≤ 1")
    lock = _review_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with lock:
        eligible = [b for b in buckets if b.get("metadata", {}).get("type") == "dynamic"
                    and not b.get("metadata", {}).get("protected")
                    and not b.get("metadata", {}).get("pinned")]
        pairs = await candidates(eligible, engine, review_threshold)
        by_id = {b["id"]: b for b in eligible}
        archived, retained, automatic, errors = set(), set(), [], []
        for pair in pairs:
            if not pair["exact"] and pair["score"] < auto_threshold:
                continue
            a, b = pair["a"], pair["b"]
            if a["id"] in archived or b["id"] in archived:
                continue
            # Keep a representative stable for this scan (A~B, B~C != A~C).
            if a["id"] in retained and b["id"] in retained:
                continue
            if b["id"] in retained or (a["id"] not in retained and
                (len(by_id[b["id"]].get("content", "")), b["id"]) >
                (len(by_id[a["id"]].get("content", "")), a["id"])):
                a, b = b, a
            try:
                rid = await _review(manager, "archive", a["id"], b["id"], a["hash"], b["hash"])
            except ValueError as exc:
                errors.append(str(exc))
                continue
            archived.add(b["id"])
            retained.add(a["id"])
            automatic.append({"id": rid, "a": a, "b": b, "score": pair["score"]})
        remaining = [p for p in pairs if p["a"]["id"] not in archived and p["b"]["id"] not in archived]
        return {"pairs": remaining, "total": len(remaining), "automatic": automatic,
                "reviewed": history(), "errors": errors,
                "review_threshold": review_threshold, "auto_threshold": auto_threshold}
