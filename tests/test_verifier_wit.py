from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.wit import WITVerifier


def test_wit_verifier_valid_and_invalid(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    comp_dir = docs_dir / "tier1_interface"
    comp_dir.mkdir()
    # Doc demanding WIT
    doc_file = comp_dir / "interface_wit.md"
    doc_file.write_text(
        """# WIT Specs {VERIFY_WIT}
## Overview
WIT specification file.
""",
        encoding="utf-8",
    )
    # Valid WIT file
    wit_dir = comp_dir / "wit"
    wit_dir.mkdir()
    (wit_dir / "fireball.wit").write_text(
        """package fireball:host@0.1.0;

interface types {
    enum recovery-strategy {
        ignore,
        retry,
        restart,
        panic,
    }
}

world fireball {
    import types;
}
""",
        encoding="utf-8",
    )
    cfg = Config()
    parser = MarkdownParser(cfg)
    doc = parser.parse_file(doc_file, docs_dir)
    verifier = WITVerifier(cfg)
    issues, results = verifier.verify_documents([doc], docs_dir)
    assert len(results) == 1
    assert results[0].status == "PASS"
    assert "types" in results[0].defined_interfaces
    assert "fireball" in results[0].defined_worlds
    assert len(issues) == 0
    # Invalid WIT file test (mismatched brace)
    (wit_dir / "invalid.wit").write_text(
        """package fireball:bad;
interface broken {
    func test() -> u32;
// missing closing brace
""",
        encoding="utf-8",
    )
    issues2, results2 = verifier.verify_documents([doc], docs_dir)
    assert any(i.rule_code == "WIT-SYNTAX-ERROR" for i in issues2)
