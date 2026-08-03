"""Liveness probe."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Unauthenticated liveness probe.

    Deliberately does nothing beyond returning success — it does not
    touch the database, so a load balancer or orchestrator can use it
    to answer "is the process up" without that check itself being
    vulnerable to database outages. A future phase's readiness/health
    endpoint that also checks database connectivity is a separate,
    additive concern.
    """
    return {"status": "ok"}
