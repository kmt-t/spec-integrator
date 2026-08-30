from spec_integrator.db import DocAuditDB


def test_db_crud():
    db = DocAuditDB(":memory:")
    # Insert Document
    db.insert_document("docs/requires/req.md", "0", "requires", "hash123")
    docs = db.get_all_documents()
    assert len(docs) == 1
    assert docs[0]["file_path"] == "docs/requires/req.md"
    assert docs[0]["tier"] == "0"
    # Insert Section
    db.insert_section(
        "sec:docs/requires/req.md#Intro",
        "docs/requires/req.md",
        "Intro",
        1,
        1,
        10,
        "Body text",
        "shash",
    )
    secs = db.get_all_sections()
    assert len(secs) == 1
    assert secs[0]["heading"] == "Intro"
    # Insert Keyword Reference
    db.insert_keyword_reference(
        "REQ_001",
        "docs/requires/req.md",
        "sec:docs/requires/req.md#Intro",
        "defines",
        1,
    )
    refs = db.get_keyword_references("REQ_001")
    assert len(refs) == 1
    assert refs[0]["relation_type"] == "defines"
    # Insert Link
    db.insert_link("docs/tier1/a.md", 5, "docs/tier2/b.md", "sec", 1)
    invalid = db.get_invalid_links()
    assert len(invalid) == 0
    # Cache
    db.set_cache("hash_key_1", "RULE_01", "target_1", "PASS", "Reason OK")
    cache = db.get_cache("hash_key_1")
    assert cache["status"] == "PASS"
    assert cache["reason"] == "Reason OK"
    db.close()
