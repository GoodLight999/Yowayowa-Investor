"""Broker execution package: proposals, interlocks, audit (P2A).

The P2B submission transport (RakutenWebSubmissionTransport) is the only
component permitted to write stage=submit audit entries, and it ships
behind the frozen submissions_enabled=False master gate (COO ruling).
"""

from __future__ import annotations

from yowayowa.broker.execution.audit import (
    AUDIT_STATE_FILE_NAME,
    AppendOnlyAuditLog,
    AuditEntry,
)
from yowayowa.broker.execution.interlocks import (
    DuplicateCheckResult,
    ExecutionInterlockDecision,
    evaluate_execution_interlocks,
)
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import (
    REQUEST_STAGE_SUBMIT,
    BrokerExecutionDomainService,
    DuplicateProposalError,
    OrderExecutionPreview,
)
from yowayowa.broker.execution.transport import (
    BrokerConnectorFeatureError,
    RakutenWebSubmissionTransport,
)

__all__ = [
    "AUDIT_STATE_FILE_NAME",
    "REQUEST_STAGE_SUBMIT",
    "AppendOnlyAuditLog",
    "AuditEntry",
    "BrokerConnectorFeatureError",
    "BrokerExecutionDomainService",
    "DuplicateCheckResult",
    "DuplicateProposalError",
    "ExecutionInterlockDecision",
    "OrderExecutionPreview",
    "OrderProposal",
    "RakutenWebSubmissionTransport",
    "evaluate_execution_interlocks",
]
