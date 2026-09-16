"""Document quality-gate implementations."""

from spec_integrator.document.gates.consistency import ConsistencyVerifier
from spec_integrator.document.gates.evidence import EvidenceVerifier
from spec_integrator.document.gates.formal import FormalVerifier
from spec_integrator.document.gates.obligation import ObligationVerifier
from spec_integrator.document.gates.section_verifier import SectionTopicVerifier
from spec_integrator.document.gates.static import StaticVerifier
from spec_integrator.document.gates.wit import WITVerifier

__all__ = [
    "ConsistencyVerifier",
    "EvidenceVerifier",
    "FormalVerifier",
    "ObligationVerifier",
    "SectionTopicVerifier",
    "StaticVerifier",
    "WITVerifier",
]
