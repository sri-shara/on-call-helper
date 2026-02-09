"""
SRE Knowledge Module for On-Call Helper.

This module provides access to SRE knowledge from the oncall repository:
- Error pattern recognition
- Tenant classification
- Infrastructure health checks
- Runbook suggestions

All knowledge is derived from the oncall handbook and runbooks.
"""

from .loader import (
    load_sre_knowledge,
    get_triage_system_prompt,
    get_knowledge_summary,
    clear_knowledge_cache,
    SREKnowledge,
)

from .error_patterns import (
    ErrorPattern,
    PatternSeverity,
    ErrorPatternMatcher,
    get_pattern_matcher,
    match_error_pattern,
    get_pattern_classification,
)

from .tenants import (
    TenantType,
    TenantInfo,
    classify_tenant_by_id,
    classify_tenant_by_name,
    is_production_tenant,
    is_demo_tenant,
    get_tenant_priority,
    get_all_production_tenant_ids,
    get_all_demo_tenant_ids,
    PRODUCTION_TENANTS,
    DEMO_TENANTS,
)

from .infrastructure import (
    HealthStatus,
    InfraCheck,
    InfraHealthReport,
    InfrastructureChecker,
    get_infrastructure_checker,
    run_quick_health_check,
)

from .runbooks import (
    RunbookSuggestion,
    RunbookSuggester,
    get_runbook_suggester,
    suggest_runbook,
    get_investigation_steps,
    get_diagnostic_commands,
)

from .pattern_learner import (
    PatternLearner,
    get_pattern_learner,
)

__all__ = [
    # Loader
    "load_sre_knowledge",
    "get_triage_system_prompt",
    "get_knowledge_summary",
    "clear_knowledge_cache",
    "SREKnowledge",
    # Error Patterns
    "ErrorPattern",
    "PatternSeverity",
    "ErrorPatternMatcher",
    "get_pattern_matcher",
    "match_error_pattern",
    "get_pattern_classification",
    # Tenants
    "TenantType",
    "TenantInfo",
    "classify_tenant_by_id",
    "classify_tenant_by_name",
    "is_production_tenant",
    "is_demo_tenant",
    "get_tenant_priority",
    "get_all_production_tenant_ids",
    "get_all_demo_tenant_ids",
    "PRODUCTION_TENANTS",
    "DEMO_TENANTS",
    # Infrastructure
    "HealthStatus",
    "InfraCheck",
    "InfraHealthReport",
    "InfrastructureChecker",
    "get_infrastructure_checker",
    "run_quick_health_check",
    # Runbooks
    "RunbookSuggestion",
    "RunbookSuggester",
    "get_runbook_suggester",
    "suggest_runbook",
    "get_investigation_steps",
    "get_diagnostic_commands",
    # Pattern Learning
    "PatternLearner",
    "get_pattern_learner",
]
