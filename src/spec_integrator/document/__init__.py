"""Public facade for document-oriented operations."""

from spec_integrator.document.facade import DocumentCheckResult, DocumentFacade
from spec_integrator.document.gates import (
    ConsistencyGate,
    EvidenceGate,
    FormalGate,
    ObligationGate,
    SectionTopicGate,
    StaticGate,
    WITGate,
)

__all__ = [
    "ConsistencyGate",
    "DocumentCheckResult",
    "DocumentFacade",
    "EvidenceGate",
    "FormalGate",
    "ObligationGate",
    "SectionTopicGate",
    "StaticGate",
    "WITGate",
]
