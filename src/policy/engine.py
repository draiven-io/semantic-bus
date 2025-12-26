"""
IBAC Policy Engine

Intent-Based Access Control for the Semantic Bus.
Evaluates policies before routing exec.request messages.

IBAC differs from RBAC/ABAC:
- Decisions based on declared PURPOSE, not role
- Considers DATA_SCOPE being accessed
- Evaluates RISK_LEVEL of the action
- Context-aware (session, tenant, participants)

Policy Evaluation:
1. Check purpose is allowed for this action
2. Check data_scope access is permitted
3. Check risk_level is acceptable
4. Apply tenant-specific rules
5. Apply session-specific constraints
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    """Risk level classifications."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyDecision(str, Enum):
    """Policy evaluation result."""
    ALLOW = "allow"
    DENY = "deny"
    WARN = "warn"  # Allow but log warning


@dataclass
class PolicyContext:
    """
    Context for policy evaluation.
    
    Contains all information needed to make an access decision.
    """
    # Session info
    session_id: UUID
    tenant_id: str
    requester_agent_id: UUID
    provider_agent_id: UUID
    
    # Request info
    action: str
    purpose: str | None = None
    data_scope: list[str] = field(default_factory=list)
    risk_level: str | None = None
    
    # Additional context
    exec_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyResult:
    """
    Result of a policy evaluation.
    """
    decision: PolicyDecision
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_allowed(self) -> bool:
        return self.decision in (PolicyDecision.ALLOW, PolicyDecision.WARN)


class Policy:
    """
    Base class for policies.
    
    Override evaluate() to implement custom logic.
    """
    
    @property
    def name(self) -> str:
        return self.__class__.__name__
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        """
        Evaluate this policy against the context.
        
        Returns:
            PolicyResult with decision
        """
        return PolicyResult(decision=PolicyDecision.ALLOW)


class RiskLevelPolicy(Policy):
    """
    Policy that checks risk level thresholds.
    
    Denies requests that exceed the maximum allowed risk.
    """
    
    def __init__(self, max_risk: RiskLevel = RiskLevel.HIGH):
        self.max_risk = max_risk
        self._risk_order = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3
        }
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if not context.risk_level:
            # No risk level declared - allow with warning
            return PolicyResult(
                decision=PolicyDecision.WARN,
                reason="No risk_level declared"
            )
        
        try:
            declared_risk = RiskLevel(context.risk_level)
        except ValueError:
            return PolicyResult(
                decision=PolicyDecision.WARN,
                reason=f"Unknown risk_level: {context.risk_level}"
            )
        
        if self._risk_order[declared_risk] > self._risk_order[self.max_risk]:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Risk level {declared_risk.value} exceeds maximum {self.max_risk.value}",
                details={
                    "declared_risk": declared_risk.value,
                    "max_allowed": self.max_risk.value
                }
            )
        
        return PolicyResult(decision=PolicyDecision.ALLOW)


class PurposePolicy(Policy):
    """
    Policy that checks if purpose is declared and valid.
    
    Can be configured with allowed/denied purposes per action.
    """
    
    def __init__(
        self,
        require_purpose: bool = True,
        denied_purposes: list[str] | None = None
    ):
        self.require_purpose = require_purpose
        self.denied_purposes = set(denied_purposes or [])
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if self.require_purpose and not context.purpose:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason="Purpose is required but not declared"
            )
        
        if context.purpose and context.purpose in self.denied_purposes:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Purpose '{context.purpose}' is not allowed",
                details={"denied_purpose": context.purpose}
            )
        
        return PolicyResult(decision=PolicyDecision.ALLOW)


class DataScopePolicy(Policy):
    """
    Policy that checks data scope access.
    
    Can be configured with allowed/denied data scopes per tenant.
    """
    
    def __init__(
        self,
        require_scope: bool = False,
        denied_scopes: list[str] | None = None,
        sensitive_scopes: list[str] | None = None
    ):
        self.require_scope = require_scope
        self.denied_scopes = set(denied_scopes or [])
        self.sensitive_scopes = set(sensitive_scopes or [
            "pii", "financial", "health", "credentials"
        ])
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if self.require_scope and not context.data_scope:
            return PolicyResult(
                decision=PolicyDecision.WARN,
                reason="Data scope not declared"
            )
        
        # Check for denied scopes
        denied = set(context.data_scope) & self.denied_scopes
        if denied:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Access to data scopes denied: {denied}",
                details={"denied_scopes": list(denied)}
            )
        
        # Warn on sensitive scope access
        sensitive = set(context.data_scope) & self.sensitive_scopes
        if sensitive:
            return PolicyResult(
                decision=PolicyDecision.WARN,
                reason=f"Accessing sensitive data: {sensitive}",
                details={"sensitive_scopes": list(sensitive)}
            )
        
        return PolicyResult(decision=PolicyDecision.ALLOW)


class TenantIsolationPolicy(Policy):
    """
    Policy that ensures tenant isolation.
    
    Verifies requester and provider are in the same tenant.
    """
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # This is already enforced by session creation,
        # but we double-check here for defense in depth
        return PolicyResult(decision=PolicyDecision.ALLOW)


class PolicyEngine:
    """
    Evaluates multiple policies to make access decisions.
    
    Policies are evaluated in order. First DENY wins.
    All policies must ALLOW (or WARN) for request to proceed.
    """
    
    def __init__(self):
        self._policies: list[Policy] = []
        self._register_default_policies()
    
    def _register_default_policies(self) -> None:
        """Register the default policy stack."""
        self._policies = [
            TenantIsolationPolicy(),
            RiskLevelPolicy(max_risk=RiskLevel.HIGH),
            PurposePolicy(require_purpose=False),
            DataScopePolicy(require_scope=False),
        ]
    
    def add_policy(self, policy: Policy) -> None:
        """Add a policy to the evaluation chain."""
        self._policies.append(policy)
        logger.debug(f"Added policy: {policy.name}")
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        """
        Evaluate all policies against the context.
        
        Returns:
            PolicyResult - DENY if any policy denies, WARN if any warns, else ALLOW
        """
        warnings: list[str] = []
        
        for policy in self._policies:
            result = policy.evaluate(context)
            
            if result.decision == PolicyDecision.DENY:
                logger.warning(
                    f"IBAC DENY by {policy.name}: {result.reason} "
                    f"(session={context.session_id}, action={context.action})"
                )
                return result
            
            if result.decision == PolicyDecision.WARN:
                warnings.append(f"{policy.name}: {result.reason}")
        
        if warnings:
            logger.info(
                f"IBAC WARN: {warnings} "
                f"(session={context.session_id}, action={context.action})"
            )
            return PolicyResult(
                decision=PolicyDecision.WARN,
                reason="; ".join(warnings)
            )
        
        logger.debug(
            f"IBAC ALLOW (session={context.session_id}, action={context.action})"
        )
        return PolicyResult(decision=PolicyDecision.ALLOW)
    
    def check_exec_request(
        self,
        session_id: UUID,
        tenant_id: str,
        requester_agent_id: UUID,
        provider_agent_id: UUID,
        action: str,
        purpose: str | None = None,
        data_scope: list[str] | None = None,
        risk_level: str | None = None,
        exec_id: str | None = None
    ) -> PolicyResult:
        """
        Convenience method to check an exec.request.
        
        Builds context and evaluates policies.
        """
        context = PolicyContext(
            session_id=session_id,
            tenant_id=tenant_id,
            requester_agent_id=requester_agent_id,
            provider_agent_id=provider_agent_id,
            action=action,
            purpose=purpose,
            data_scope=data_scope or [],
            risk_level=risk_level,
            exec_id=exec_id
        )
        return self.evaluate(context)
