"""
Tenant Classification Module.

Uses explicit tenant lists from the oncall repository to classify
tenants as production or demo/internal.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Set


class TenantType(str, Enum):
    """Types of tenants."""
    PRODUCTION = "production"  # Real customers, errors require investigation
    DEMO = "demo"  # Demo/internal tenants, errors are usually noise
    UNKNOWN = "unknown"  # Not in known lists


@dataclass
class TenantInfo:
    """Information about a tenant."""
    tenant_id: str
    name: str
    tenant_type: TenantType
    notes: Optional[str] = None


# Demo/Internal Tenants - errors from these are usually noise
DEMO_TENANTS: dict[str, TenantInfo] = {
    "04d3229f-7097-4af3-86df-37e29775d146": TenantInfo(
        tenant_id="04d3229f-7097-4af3-86df-37e29775d146",
        name="TENEX POC Demo (tnxdmo)",
        tenant_type=TenantType.DEMO,
        notes="Demo tenant"
    ),
    "6af91f8f-8dcb-43c8-9540-c48a9acd0003": TenantInfo(
        tenant_id="6af91f8f-8dcb-43c8-9540-c48a9acd0003",
        name="Tenex Demo",
        tenant_type=TenantType.DEMO,
        notes="Has secops_id=EXTDEMO"
    ),
    "6af91f8f-8dcb-43c8-9540-c48a9acd0005": TenantInfo(
        tenant_id="6af91f8f-8dcb-43c8-9540-c48a9acd0005",
        name="TENEX POC MSSP (tnxmx)",
        tenant_type=TenantType.DEMO,
        notes="MSSP demo"
    ),
    "3a3e2117-cc46-45e8-8092-a963ad9cb6d7": TenantInfo(
        tenant_id="3a3e2117-cc46-45e8-8092-a963ad9cb6d7",
        name="TENEX Internal",
        tenant_type=TenantType.DEMO,
        notes="Internal testing"
    ),
    "b1545931-5243-4fb4-afb7-a4d92633ffee": TenantInfo(
        tenant_id="b1545931-5243-4fb4-afb7-a4d92633ffee",
        name="Tenex Sandbox",
        tenant_type=TenantType.DEMO,
        notes="Sandbox environment"
    ),
}

# Production Customer Tenants - errors require investigation
PRODUCTION_TENANTS: dict[str, TenantInfo] = {
    "d3f5c2f0-8e11-4ff7-a8cd-a767521ac891": TenantInfo(
        tenant_id="d3f5c2f0-8e11-4ff7-a8cd-a767521ac891",
        name="Whitney",
        tenant_type=TenantType.PRODUCTION
    ),
    "ceecf0a5-708e-4eb4-9830-7c88c23d949b": TenantInfo(
        tenant_id="ceecf0a5-708e-4eb4-9830-7c88c23d949b",
        name="Horizontal",
        tenant_type=TenantType.PRODUCTION
    ),
    "632d61b2-c34e-4c96-95a2-f54d72ef1c3f": TenantInfo(
        tenant_id="632d61b2-c34e-4c96-95a2-f54d72ef1c3f",
        name="Bowtie",
        tenant_type=TenantType.PRODUCTION
    ),
    "084166c3-3803-4ea1-8fe1-38891e896fd2": TenantInfo(
        tenant_id="084166c3-3803-4ea1-8fe1-38891e896fd2",
        name="Sycamore",
        tenant_type=TenantType.PRODUCTION
    ),
    "05211d5c-e216-45cb-952a-52698e25501b": TenantInfo(
        tenant_id="05211d5c-e216-45cb-952a-52698e25501b",
        name="Haven",
        tenant_type=TenantType.PRODUCTION
    ),
    "3ca0ca5f-6678-4bc5-9165-9fb725809181": TenantInfo(
        tenant_id="3ca0ca5f-6678-4bc5-9165-9fb725809181",
        name="RedRock",
        tenant_type=TenantType.PRODUCTION
    ),
    "3513fadf-58d9-4224-bb53-14b0b6d216a0": TenantInfo(
        tenant_id="3513fadf-58d9-4224-bb53-14b0b6d216a0",
        name="Warehouse",
        tenant_type=TenantType.PRODUCTION
    ),
    "83cbac04-bac2-4352-a44a-3af394edd0b0": TenantInfo(
        tenant_id="83cbac04-bac2-4352-a44a-3af394edd0b0",
        name="IPA",
        tenant_type=TenantType.PRODUCTION,
        notes="Highest volume tenant"
    ),
    "0b3e2302-0861-43b8-b1d4-6c390a8b8ef4": TenantInfo(
        tenant_id="0b3e2302-0861-43b8-b1d4-6c390a8b8ef4",
        name="Royals",
        tenant_type=TenantType.PRODUCTION
    ),
    "e7dc7cd9-b0b6-4670-aade-5c0d97c3c2e8": TenantInfo(
        tenant_id="e7dc7cd9-b0b6-4670-aade-5c0d97c3c2e8",
        name="Kingpins",
        tenant_type=TenantType.PRODUCTION
    ),
    "02717a42-3903-414f-a1f7-aaadd3bd9574": TenantInfo(
        tenant_id="02717a42-3903-414f-a1f7-aaadd3bd9574",
        name="QuantumLeap",
        tenant_type=TenantType.PRODUCTION
    ),
}

# Tenant name patterns that indicate demo/test
DEMO_NAME_PATTERNS: Set[str] = {
    "poc", "demo", "test", "sandbox", "internal", "dev", "staging"
}

# Production tenant names for quick lookup
PRODUCTION_TENANT_NAMES: Set[str] = {
    info.name.lower() for info in PRODUCTION_TENANTS.values()
}


def classify_tenant_by_id(tenant_id: str) -> TenantInfo:
    """
    Classify a tenant by its ID.

    Args:
        tenant_id: The tenant UUID

    Returns:
        TenantInfo with classification
    """
    # Check explicit production list
    if tenant_id in PRODUCTION_TENANTS:
        return PRODUCTION_TENANTS[tenant_id]

    # Check explicit demo list
    if tenant_id in DEMO_TENANTS:
        return DEMO_TENANTS[tenant_id]

    # Unknown tenant
    return TenantInfo(
        tenant_id=tenant_id,
        name="Unknown",
        tenant_type=TenantType.UNKNOWN,
        notes="Not in known tenant lists"
    )


def classify_tenant_by_name(tenant_name: str) -> TenantType:
    """
    Classify a tenant by its name using pattern matching.

    Args:
        tenant_name: The tenant name

    Returns:
        TenantType classification
    """
    if not tenant_name:
        return TenantType.UNKNOWN

    name_lower = tenant_name.lower()

    # Check if name matches a known production tenant
    if name_lower in PRODUCTION_TENANT_NAMES:
        return TenantType.PRODUCTION

    # Check for demo/test patterns in name
    for pattern in DEMO_NAME_PATTERNS:
        if pattern in name_lower:
            return TenantType.DEMO

    # Check explicit lists by name
    for info in PRODUCTION_TENANTS.values():
        if info.name.lower() == name_lower:
            return TenantType.PRODUCTION

    for info in DEMO_TENANTS.values():
        if info.name.lower() == name_lower:
            return TenantType.DEMO

    return TenantType.UNKNOWN


def is_production_tenant(tenant_id: Optional[str] = None, tenant_name: Optional[str] = None) -> bool:
    """
    Check if a tenant is a production tenant.

    Args:
        tenant_id: The tenant UUID (preferred)
        tenant_name: The tenant name (fallback)

    Returns:
        True if production tenant, False otherwise
    """
    if tenant_id:
        info = classify_tenant_by_id(tenant_id)
        return info.tenant_type == TenantType.PRODUCTION

    if tenant_name:
        return classify_tenant_by_name(tenant_name) == TenantType.PRODUCTION

    return False


def is_demo_tenant(tenant_id: Optional[str] = None, tenant_name: Optional[str] = None) -> bool:
    """
    Check if a tenant is a demo/internal tenant.

    Args:
        tenant_id: The tenant UUID (preferred)
        tenant_name: The tenant name (fallback)

    Returns:
        True if demo tenant, False otherwise
    """
    if tenant_id:
        info = classify_tenant_by_id(tenant_id)
        return info.tenant_type == TenantType.DEMO

    if tenant_name:
        return classify_tenant_by_name(tenant_name) == TenantType.DEMO

    return False


def get_tenant_priority(tenant_id: Optional[str] = None, tenant_name: Optional[str] = None) -> int:
    """
    Get priority level for a tenant (higher = more important).

    Args:
        tenant_id: The tenant UUID
        tenant_name: The tenant name

    Returns:
        Priority level: 3=production, 2=unknown, 1=demo
    """
    if is_production_tenant(tenant_id, tenant_name):
        return 3
    elif is_demo_tenant(tenant_id, tenant_name):
        return 1
    else:
        return 2  # Unknown - treat with caution


def get_all_production_tenant_ids() -> Set[str]:
    """Get all known production tenant IDs."""
    return set(PRODUCTION_TENANTS.keys())


def get_all_demo_tenant_ids() -> Set[str]:
    """Get all known demo tenant IDs."""
    return set(DEMO_TENANTS.keys())
