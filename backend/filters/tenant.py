"""
Tenant filter for On Call Helper.

Identifies demo/test tenants whose errors are typically noise vs
production tenants whose errors require investigation.

Tenant information sourced from:
/Users/sri/oncall/.claude/commands/sre-triage/tenant-reference.md
"""

from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel


class TenantInfo(BaseModel):
    """Information about a tenant."""

    tenant_id: str
    name: str
    is_production: bool
    notes: Optional[str] = None


# Demo/Internal tenants - errors are usually noise
# Source: /Users/sri/oncall/.claude/commands/sre-triage/tenant-reference.md
DEMO_TENANTS: Dict[str, TenantInfo] = {
    "04d3229f-7097-4af3-86df-37e29775d146": TenantInfo(
        tenant_id="04d3229f-7097-4af3-86df-37e29775d146",
        name="TENEX POC Demo (tnxdmo)",
        is_production=False,
        notes="Demo tenant",
    ),
    "6af91f8f-8dcb-43c8-9540-c48a9acd0003": TenantInfo(
        tenant_id="6af91f8f-8dcb-43c8-9540-c48a9acd0003",
        name="Tenex Demo",
        is_production=False,
        notes="Has secops_id=EXTDEMO",
    ),
    "6af91f8f-8dcb-43c8-9540-c48a9acd0005": TenantInfo(
        tenant_id="6af91f8f-8dcb-43c8-9540-c48a9acd0005",
        name="TENEX POC MSSP (tnxmx)",
        is_production=False,
        notes="MSSP demo",
    ),
    "3a3e2117-cc46-45e8-8092-a963ad9cb6d7": TenantInfo(
        tenant_id="3a3e2117-cc46-45e8-8092-a963ad9cb6d7",
        name="TENEX Internal",
        is_production=False,
        notes="Internal testing",
    ),
    "b1545931-5243-4fb4-afb7-a4d92633ffee": TenantInfo(
        tenant_id="b1545931-5243-4fb4-afb7-a4d92633ffee",
        name="Tenex Sandbox",
        is_production=False,
        notes="Sandbox environment",
    ),
}

# Production customer tenants - errors require investigation
# Source: /Users/sri/oncall/.claude/commands/sre-triage/tenant-reference.md
PRODUCTION_TENANTS: Dict[str, TenantInfo] = {
    "d3f5c2f0-8e11-4ff7-a8cd-a767521ac891": TenantInfo(
        tenant_id="d3f5c2f0-8e11-4ff7-a8cd-a767521ac891",
        name="Whitney",
        is_production=True,
    ),
    "ceecf0a5-708e-4eb4-9830-7c88c23d949b": TenantInfo(
        tenant_id="ceecf0a5-708e-4eb4-9830-7c88c23d949b",
        name="Horizontal",
        is_production=True,
    ),
    "632d61b2-c34e-4c96-95a2-f54d72ef1c3f": TenantInfo(
        tenant_id="632d61b2-c34e-4c96-95a2-f54d72ef1c3f",
        name="Bowtie",
        is_production=True,
    ),
    "084166c3-3803-4ea1-8fe1-38891e896fd2": TenantInfo(
        tenant_id="084166c3-3803-4ea1-8fe1-38891e896fd2",
        name="Sycamore",
        is_production=True,
    ),
    "05211d5c-e216-45cb-952a-52698e25501b": TenantInfo(
        tenant_id="05211d5c-e216-45cb-952a-52698e25501b",
        name="Haven",
        is_production=True,
    ),
    "3ca0ca5f-6678-4bc5-9165-9fb725809181": TenantInfo(
        tenant_id="3ca0ca5f-6678-4bc5-9165-9fb725809181",
        name="RedRock",
        is_production=True,
    ),
    "3513fadf-58d9-4224-bb53-14b0b6d216a0": TenantInfo(
        tenant_id="3513fadf-58d9-4224-bb53-14b0b6d216a0",
        name="Warehouse",
        is_production=True,
    ),
    "83cbac04-bac2-4352-a44a-3af394edd0b0": TenantInfo(
        tenant_id="83cbac04-bac2-4352-a44a-3af394edd0b0",
        name="IPA",
        is_production=True,
    ),
    "0b3e2302-0861-43b8-b1d4-6c390a8b8ef4": TenantInfo(
        tenant_id="0b3e2302-0861-43b8-b1d4-6c390a8b8ef4",
        name="Royals",
        is_production=True,
    ),
    "e7dc7cd9-b0b6-4670-aade-5c0d97c3c2e8": TenantInfo(
        tenant_id="e7dc7cd9-b0b6-4670-aade-5c0d97c3c2e8",
        name="Kingpins",
        is_production=True,
    ),
    "02717a42-3903-414f-a1f7-aaadd3bd9574": TenantInfo(
        tenant_id="02717a42-3903-414f-a1f7-aaadd3bd9574",
        name="QuantumLeap",
        is_production=True,
    ),
}

# Name patterns that indicate demo/test tenants
DEMO_NAME_PATTERNS: List[str] = [
    "poc",
    "demo",
    "test",
    "sandbox",
    "internal",
    "dev",
    "staging",
]


def get_tenant_info(tenant_id: Optional[str] = None, tenant_name: Optional[str] = None) -> Optional[TenantInfo]:
    """
    Get tenant information by ID or name.

    Args:
        tenant_id: The tenant UUID
        tenant_name: The tenant name

    Returns:
        TenantInfo if found, None otherwise
    """
    # Check by ID first
    if tenant_id:
        if tenant_id in DEMO_TENANTS:
            return DEMO_TENANTS[tenant_id]
        if tenant_id in PRODUCTION_TENANTS:
            return PRODUCTION_TENANTS[tenant_id]

    # Check by name
    if tenant_name:
        name_lower = tenant_name.lower()
        for tenant in list(DEMO_TENANTS.values()) + list(PRODUCTION_TENANTS.values()):
            if tenant.name.lower() == name_lower or name_lower in tenant.name.lower():
                return tenant

    return None


def is_demo_tenant(tenant_id: Optional[str] = None, tenant_name: Optional[str] = None) -> bool:
    """
    Check if a tenant is a demo/test tenant.

    Uses both explicit tenant list and name pattern matching.
    """
    # Check explicit demo tenant list
    if tenant_id and tenant_id in DEMO_TENANTS:
        return True

    # Check by tenant info lookup
    info = get_tenant_info(tenant_id, tenant_name)
    if info and not info.is_production:
        return True

    # Check name patterns for unknown tenants
    if tenant_name:
        name_lower = tenant_name.lower()
        for pattern in DEMO_NAME_PATTERNS:
            if pattern in name_lower:
                return True

    return False


def is_production_tenant(tenant_id: Optional[str] = None, tenant_name: Optional[str] = None) -> bool:
    """
    Check if a tenant is a known production tenant.

    Note: Unknown tenants are NOT automatically considered production.
    Use should_process_tenant() for filtering decisions.
    """
    if tenant_id and tenant_id in PRODUCTION_TENANTS:
        return True

    info = get_tenant_info(tenant_id, tenant_name)
    if info and info.is_production:
        return True

    return False


def should_process_tenant(
    tenant_id: Optional[str] = None,
    tenant_name: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Determine if errors from this tenant should be processed.

    Decision logic:
    1. Known demo tenant → Skip (return False)
    2. Known production tenant → Process (return True)
    3. Name matches demo patterns → Skip (return False)
    4. Unknown tenant → Process with caution (return True)

    Args:
        tenant_id: The tenant UUID
        tenant_name: The tenant name

    Returns:
        Tuple of (should_process, reason)
    """
    # No tenant context - process it (could be infrastructure error)
    if not tenant_id and not tenant_name:
        return True, "No tenant context - processing as infrastructure error"

    # Check if it's a known demo tenant
    if tenant_id and tenant_id in DEMO_TENANTS:
        tenant = DEMO_TENANTS[tenant_id]
        return False, f"Demo tenant: {tenant.name} - {tenant.notes or 'ignoring'}"

    # Check if it's a known production tenant
    if tenant_id and tenant_id in PRODUCTION_TENANTS:
        tenant = PRODUCTION_TENANTS[tenant_id]
        return True, f"Production tenant: {tenant.name} - requires investigation"

    # Check name patterns for unknown tenants
    if tenant_name:
        name_lower = tenant_name.lower()
        for pattern in DEMO_NAME_PATTERNS:
            if pattern in name_lower:
                return False, f"Demo tenant (name contains '{pattern}'): {tenant_name}"

    # Unknown tenant - process with caution
    identifier = tenant_id or tenant_name
    return True, f"Unknown tenant ({identifier}) - processing with caution"


def list_production_tenants() -> List[TenantInfo]:
    """Get list of all production tenants."""
    return list(PRODUCTION_TENANTS.values())


def list_demo_tenants() -> List[TenantInfo]:
    """Get list of all demo tenants."""
    return list(DEMO_TENANTS.values())
