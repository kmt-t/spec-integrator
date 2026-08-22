from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser, ParsedDocument
from spec_integrator.verifier.topology import TopologyVerifier


def test_topology_verifier_detects_acyclic_graph(tmp_path):
    config = Config()
    verifier = TopologyVerifier(config)

    md_content = """# IPC Router
## 4.2 トポロジ
```mermaid
graph TD
    Client[Client App] --> IPCR[IPC Router]
    IPCR --> ServiceA[Core Service]
    IPCR --> ServiceB[Platform HAL]
    ServiceA --> ServiceB
```
"""
    file_path = tmp_path / "components" / "tier1_interface" / "ipc_router.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(md_content, encoding="utf-8")

    parser = MarkdownParser(config)
    doc = parser.parse_file(file_path, tmp_path)

    issues, results = verifier.verify_documents([doc], tmp_path)

    assert len(issues) == 0
    assert len(results) >= 1
    assert all(r.is_acyclic for r in results)


def test_topology_verifier_catches_cycle_mutation(tmp_path):
    """Mutation testing: Injecting a circular dependency must fail the Topology gate."""
    config = Config()
    verifier = TopologyVerifier(config)

    # Cyclic dependency: TaskA -> TaskB -> TaskC -> TaskA
    cyclic_md = """# Dangerous IPC Topology
## 1. Circular Communication Topology
```mermaid
%% channel_topology
graph TD
    TaskA --> TaskB
    TaskB --> TaskC
    TaskC --> TaskA
```
"""
    file_path = tmp_path / "components" / "tier1_interface" / "ipc_cyclic.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(cyclic_md, encoding="utf-8")

    parser = MarkdownParser(config)
    doc = parser.parse_file(file_path, tmp_path)

    issues, results = verifier.verify_documents([doc], tmp_path)

    assert len(issues) == 1
    assert issues[0].gate == "Topology"
    assert issues[0].rule_code == "TOPOLOGY-CYCLE-DETECTED"
    assert all(node in issues[0].message for node in ["TaskA", "TaskB", "TaskC"])
    assert any(not r.is_acyclic for r in results)
