"""Document quality-gate implementations."""

from spec_integrator.document.gates.consistency import ConsistencyGate
from spec_integrator.document.gates.evidence import EvidenceGate
from spec_integrator.document.gates.formal import FormalGate
from spec_integrator.document.gates.identifiers import IdentifierGate
from spec_integrator.document.gates.obligation import ObligationGate
from spec_integrator.document.gates.section_topic import SectionTopicGate
from spec_integrator.document.gates.static import StaticGate
from spec_integrator.document.gates.wit import WITGate

__all__ = [
    "ConsistencyGate",
    "EvidenceGate",
    "FormalGate",
    "IdentifierGate",
    "ObligationGate",
    "SectionTopicGate",
    "StaticGate",
    "WITGate",
]
