"""Public facade for document-oriented operations."""

from spec_integrator.document.facade import DocumentCheckResult, DocumentFacade
from spec_integrator.document.gates import (
    ConsistencyVerifier,
    EvidenceVerifier,
    FormalVerifier,
    ObligationVerifier,
    SectionTopicVerifier,
    StaticVerifier,
    WITVerifier,
)

__all__ = [
    "ConsistencyVerifier",
    "DocumentCheckResult",
    "DocumentFacade",
    "EvidenceVerifier",
    "FormalVerifier",
    "ObligationVerifier",
    "SectionTopicVerifier",
    "StaticVerifier",
    "WITVerifier",
]
