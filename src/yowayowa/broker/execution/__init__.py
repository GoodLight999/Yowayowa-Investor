"""Broker execution domain package (P2A): proposals, interlocks, audit.

NO submit/execute method, NO network, NO browser transport lives here.
The Rakuten submission connector is the NEXT task.
"""

from __future__ import annotations

from yowayowa.broker.execution.audit import AppendOnlyAuditLog, AuditEntry
from yowayowa.broker.execution.interlocks import (
    DuplicateCheckResult,
    ExecutionInterlockDecision,
    evaluate_execution_interlocks,
)
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import (
    REQUEST_STAGE_SUBMIT,
    BrokerExecutionDomainService,
    OrderExecutionPreview,
)

__all__ = [
    "REQUEST_STAGE_SUBMIT",
    "AppendOnlyAuditLog",
    "AuditEntry",
    "BrokerExecutionDomainService",
    "DuplicateCheckResult",
    "ExecutionInterlockDecision",
    "OrderExecutionPreview",
    "OrderProposal",
    "evaluate_execution_interlocks",
]
