"""Execution-backed verification of review findings.

A finding that an LLM asserted and a second LLM agreed with is still just an
opinion. This package tries to *demonstrate* a finding — by tracing it
deterministically, or by running code that exercises it — so the report can
separate what we can stand behind from what we merely suspect.
"""

from app.proof.models import ProofResult, ProofStatus

__all__ = ["ProofResult", "ProofStatus"]
