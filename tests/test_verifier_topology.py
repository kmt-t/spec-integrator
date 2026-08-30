from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.topology import TopologyVerifier


def test_topology_verifier_detects_acyclic_graph(tmp_path):
    config = Config()
    verifier = TopologyVerifier(config)
    md_content = """# IPC Router
## 4.2 Communication Topology
```mermaid
%% topology
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


def test_topology_verifier_extracts_and_verifies_role_matrix_table(tmp_path):
    """Verifies that role matrix Markdown tables are dynamically parsed and checked."""
    config = Config()
    verifier = TopologyVerifier(config)
    # Valid acyclic matrix
    valid_md = """# IPC Router
## 4.1.1 Role-based Access Control
#### ロール間通信許可マトリクス (FB_CONF_ROUTER_ROLE_MATRIX)
| Sender \\ Target | CORE_SERVICE | PLATFORM_HAL | DEBUGGER |
| :--- | :---: | :---: | :---: |
| **CLIENT_APP** | ALLOW | ALLOW | DENY |
| **CORE_SERVICE** | - | ALLOW | DENY |
| **DEBUGGER** | ALLOW | ALLOW | - |
"""
    file_path = tmp_path / "components" / "tier1_interface" / "ipc_router.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(valid_md, encoding="utf-8")
    parser = MarkdownParser(config)
    doc = parser.parse_file(file_path, tmp_path)
    issues, results = verifier.verify_documents([doc], tmp_path)
    assert len(issues) == 0
    matrix_results = [
        r for r in results if "Role Matrix" in r.graph_name or "IPC Router" in r.graph_name
    ]
    assert len(matrix_results) == 1
    assert matrix_results[0].is_acyclic
    assert ("CLIENT_APP", "CORE_SERVICE") in matrix_results[0].edges


def test_topology_verifier_catches_role_matrix_cycle_mutation(tmp_path):
    """Mutation testing: Injecting a circular dependency in the Role Matrix table must fail."""
    config = Config()
    verifier = TopologyVerifier(config)
    # Mutated cyclic matrix: CLIENT -> CORE -> PLATFORM -> CLIENT
    cyclic_md = """# IPC Router
## 4.1.1 Role-based Access Control
#### ロール間通信許可マトリクス (FB_CONF_ROUTER_ROLE_MATRIX)
| Sender \\ Target | CORE_SERVICE | PLATFORM_HAL | CLIENT_APP |
| :--- | :---: | :---: | :---: |
| **CLIENT_APP** | ALLOW | DENY | DENY |
| **CORE_SERVICE** | DENY | ALLOW | DENY |
| **PLATFORM_HAL** | DENY | DENY | ALLOW |
"""
    file_path = tmp_path / "components" / "tier1_interface" / "ipc_router.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(cyclic_md, encoding="utf-8")
    parser = MarkdownParser(config)
    doc = parser.parse_file(file_path, tmp_path)
    issues, results = verifier.verify_documents([doc], tmp_path)
    assert len(issues) == 1
    assert issues[0].gate == "Topology"
    assert issues[0].rule_code == "TOPOLOGY-CYCLE-DETECTED"
    assert any(not r.is_acyclic for r in results)


def test_topology_verifier_honors_explicit_opt_out(tmp_path):
    """Verifies that diagrams with explicit opt-out annotations (%% not-a-topology) are skipped."""
    config = Config()
    verifier = TopologyVerifier(config)
    # Cyclic control loop with explicit opt-out
    opt_out_md = """# Algorithm Loop
## 1. Internal Pipeline Loop
```mermaid
%% not-a-topology: Internal execution loop within single coroutine
graph TD
    StepA --> StepB
    StepB --> StepC
    StepC --> StepA
```
"""
    file_path = tmp_path / "components" / "tier2_runtime" / "loop.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(opt_out_md, encoding="utf-8")
    parser = MarkdownParser(config)
    doc = parser.parse_file(file_path, tmp_path)
    issues, results = verifier.verify_documents([doc], tmp_path)
    assert len(issues) == 0
    assert len(results) == 0
