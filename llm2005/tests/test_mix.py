"""Testy vyvážené směsi, tréninku tokenizéru s průběhem a serveru přehledu."""
import json
import random
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

from llm2005.db import Database
from llm2005.document import Document
from llm2005.mix import MixConfig, allocate, iter_mix
from llm2005.tokenizer import train_mixed

WORDS = "the meeting is moved to friday please call me about the energy contract blog today lol court".split()


def make_db(path, sizes: dict[str, int], doc_len: int = 100):
    """Malá databáze: sizes = počet dokumentů na zdroj, každý dokument má doc_len znaků."""
    db = Database(path)
    rng = random.Random(1)
    for source, n in sizes.items():
        for i in range(n):
            text = ""
            while len(text) < doc_len:
                text += rng.choice(WORDS) + " "
            db.add(Document(source=source, source_id=str(i), title=None, date="2005-01-01", url=None,
                            text=text[:doc_len]))
    db.commit()
    db.close()
    return str(path)


def run_mix(db_path, cfg):
    conn = sqlite3.connect(db_path)
    out = list(iter_mix(conn, cfg))
    conn.close()
    return out


def shares(items):
    chars: dict[str, int] = {}
    for s, _, text in items:
        chars[s] = chars.get(s, 0) + len(text)
    return chars


def test_allocate_proportional_and_redistribute():
    w = {"a": 50, "b": 30, "c": 20}
    assert allocate(1000, w, {"a": 10**6, "b": 10**6, "c": 10**6}) == {"a": 500, "b": 300, "c": 200}
    q = allocate(1000, w, {"a": 10**6, "b": 10**6, "c": 50})  # c nemá dost -> celý, zbytek poměrně a:b = 5:3
    assert q["c"] == 50
    assert q["a"] == int(950 * 5 / 8) and q["b"] == int(950 * 3 / 8)


def test_mix_shares_follow_weights(tmp_path):
    db = make_db(tmp_path / "m.sqlite", {"usenet": 400, "wikipedia": 400, "blogs": 400, "arxiv": 5})
    cfg = MixConfig(seed=2005, max_chars=30_000, weights={"usenet": 50, "wikipedia": 30, "blogs": 20}, whole=["arxiv"])
    ch = shares(run_mix(db, cfg))
    assert ch["arxiv"] == 5 * 100  # malý zdroj celý
    rest = 30_000 - 500
    for s, w in {"usenet": 50, "wikipedia": 30, "blogs": 20}.items():
        assert abs(ch[s] - rest * w / 100) <= 100  # odchylka max. jeden dokument
    assert sum(ch.values()) <= 30_000 + 3 * 100


def test_mix_short_source_taken_whole_rest_redistributed(tmp_path):
    db = make_db(tmp_path / "m.sqlite", {"usenet": 400, "wikipedia": 400, "pg19": 10})
    cfg = MixConfig(seed=2005, max_chars=20_000, weights={"usenet": 40, "wikipedia": 40, "pg19": 20}, whole=[])
    ch = shares(run_mix(db, cfg))
    assert ch["pg19"] == 10 * 100  # chtěl 4 000, má jen 1 000 -> celý
    assert abs(ch["usenet"] - 9_500) <= 100 and abs(ch["wikipedia"] - 9_500) <= 100


def test_mix_is_deterministic_and_whole_documents(tmp_path):
    db = make_db(tmp_path / "m.sqlite", {"usenet": 300, "blogs": 300})
    cfg = MixConfig(seed=2005, max_chars=10_000, weights={"usenet": 1, "blogs": 1})
    a, b = run_mix(db, cfg), run_mix(db, cfg)
    assert [(s, i) for s, i, _ in a] == [(s, i) for s, i, _ in b]
    assert all(len(t) == 100 for _, _, t in a)  # celé dokumenty, nic se neřeže
    other = run_mix(db, MixConfig(seed=7, max_chars=10_000, weights={"usenet": 1, "blogs": 1}))
    assert {i for _, i, _ in other} != {i for _, i, _ in a}  # jiný seed = jiný výběr


def test_train_mixed_writes_tokenizer_status_and_report(tmp_path):
    db = make_db(tmp_path / "m.sqlite", {"usenet": 200, "blogs": 200, "arxiv": 3}, doc_len=400)
    cfg = MixConfig(seed=2005, max_chars=60_000, weights={"usenet": 1, "blogs": 1}, whole=["arxiv"])
    out, prog = tmp_path / "tok.json", tmp_path / "progress"
    report = train_mixed(db, cfg, 400, out, tmp_path / "sample", prog, eval_chars=5_000)
    assert out.exists()
    status = json.loads((prog / "tokenizer-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done" and status["percent"] == 100
    assert [p["state"] for p in status["phases"]] == ["done"] * 5
    assert {s["source"] for s in status["sources"]} == {"usenet", "blogs", "arxiv"}
    saved = json.loads((prog / "tokenizer-report.json").read_text(encoding="utf-8"))
    assert saved["vocab_size"] == report["vocab_size"] <= 400
    assert len(saved["samples"]) == 5 and all(s["roundtrip_ok"] for s in saved["samples"])
    assert saved["chars_per_token"]["mix_weighted"] > 1
    # druhý běh použije hotový vzorek (fáze vzorkování přeskočena)
    train_mixed(db, cfg, 400, out, tmp_path / "sample", prog, eval_chars=5_000)
    status = json.loads((prog / "tokenizer-status.json").read_text(encoding="utf-8"))
    assert status["phases"][0]["state"] == "skipped"


def test_mix_clean_skips_short_and_counts_cleaned(tmp_path):
    db = Database(tmp_path / "c.sqlite")
    noise = "\n".join("M" + "8" * 60 for _ in range(20))  # uuencode -> po čištění skoro nic
    db.add(Document(source="usenet", source_id="bin", title=None, date="1990-01-01", url=None, text="hi\n" + noise))
    db.add(Document(source="usenet", source_id="ok", title=None, date="1990-01-01", url=None,
                    text="real text " * 40 + "-" * 50))
    db.commit()
    db.close()
    conn = sqlite3.connect(tmp_path / "c.sqlite")
    cursors: dict = {}
    out = list(iter_mix(conn, MixConfig(seed=1, max_chars=10_000, weights={"usenet": 1}), clean=True, cursors=cursors))
    assert [t for _, _, t in out] == ["real text " * 40 + "-" * 8]
    st = cursors["usenet"].stats()
    assert st["dropped_docs"] == 1 and st["removed"]["uuencode"] > 1000 and st["removed"]["repeats"] == 42


def test_prepare_from_sample_and_db(tmp_path):
    import numpy as np
    from llm2005.prepare import prepare

    db = make_db(tmp_path / "p.sqlite", {"usenet": 300, "blogs": 300}, doc_len=400)
    cfg = MixConfig(seed=2005, max_chars=100_000, weights={"usenet": 1, "blogs": 1})
    prog = tmp_path / "progress"
    train_mixed(db, cfg, 400, tmp_path / "tok.json", tmp_path / "sample", prog, eval_chars=5_000)
    a = prepare(db, cfg, str(tmp_path / "tok.json"), tmp_path / "a", tmp_path / "sample", prog, val_fraction=0.05)
    b = prepare(db, cfg, str(tmp_path / "tok.json"), tmp_path / "b", tmp_path / "no-sample", prog, val_fraction=0.05)
    for r in (a, b):
        assert r["tokens_val"] > 0 and r["tokens_train"] > 10 * r["tokens_val"]
    # vzorek i výběr z databáze dají stejnou směs -> stejné tokeny
    assert (np.fromfile(tmp_path / "a" / "train.bin", dtype=np.uint16) ==
            np.fromfile(tmp_path / "b" / "train.bin", dtype=np.uint16)).all()
    status = json.loads((prog / "prepare-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done" and status["kind"] == "prepare" and status["result"]["tokens_train"] == b["tokens_train"]


def test_progress_server_is_local_and_read_only(tmp_path):
    from llm2005.progress_server import make_server

    index = tmp_path / "index.html"
    index.write_text("<h1>ok</h1>", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    (data / "tokenizer-status.json").write_text('{"state": "running"}', encoding="utf-8")
    (tmp_path / "secret.json").write_text("tajne", encoding="utf-8")
    srv = make_server(0, index, data)
    host, port = srv.server_address[:2]
    assert host == "127.0.0.1"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        assert urllib.request.urlopen(base + "/").read() == b"<h1>ok</h1>"
        assert json.loads(urllib.request.urlopen(base + "/progress/tokenizer-status.json").read())["state"] == "running"
        for bad in ("/progress/../secret.json", "/progress/%2e%2e/secret.json", "/secret.json", "/progress/", "/data/x"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad)
            assert e.value.code == 404
        req = urllib.request.Request(base + "/progress/tokenizer-status.json", data=b"x", method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 501
    finally:
        srv.shutdown()
        srv.server_close()
