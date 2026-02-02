"""
Service Filter for On Call Helper.

Classifies services to filter out infrastructure noise and prioritize
Nucleus MDR platform errors that require attention.
"""

from typing import Set, Tuple


# Nucleus MDR services - high priority, always process
NUCLEUS_SERVICES: Set[str] = {
    # Core services
    "caseservice",
    "alertservice",
    "casetierservice",
    "casemaker",

    # Event writers/processors
    "caseeventwriter",
    "alertsbatchwriter",
    "batchwriter",
    "eventwriter",

    # Entity processing
    "entityenricher",
    "entityextractor",

    # Integrations
    "secops-sync",
    "secops-integration",
    "soar-integration",
    "chronicle-alerts-fetcher",
    "chronicle-fetcher",

    # API services
    "api-gateway",
    "nucleus-api",
    "graphql",

    # Background workers
    "tier-processor",
    "playbook-runner",
    "enrichment-worker",
}

# Kubernetes infrastructure - low priority, skip unless CRITICAL
K8S_INFRA_SERVICES: Set[str] = {
    # Monitoring
    "prometheus",
    "prometheus-server",
    "prometheus-operator",
    "kube-state-metrics",
    "node-exporter",
    "alertmanager",
    "grafana",
    "thanos",

    # Logging
    "fluentd",
    "fluentbit",
    "fluent-bit",
    "loki",
    "promtail",

    # Networking
    "ingress-nginx",
    "nginx-ingress",
    "external-dns",
    "coredns",
    "kube-dns",
    "calico",
    "cilium",

    # Certificate management
    "cert-manager",

    # Storage
    "csi-driver",
    "storage-provisioner",

    # Kubernetes system
    "kube-scheduler",
    "kube-controller",
    "kube-apiserver",
    "etcd",
    "kube-proxy",

    # GKE specific
    "gke-metrics-agent",
    "fluentd-gke",
    "event-exporter",
    "stackdriver",
    "metadata-agent",
    "pdcsi-node",
}

# Resource types to filter (GCP resource types)
K8S_INFRA_RESOURCE_TYPES: Set[str] = {
    "k8s_node",
    "gke_nodepool",
    "k8s_cluster",
}


def classify_service(service_name: str) -> Tuple[str, int]:
    """
    Classify a service and return its category and priority.

    Args:
        service_name: The service name to classify

    Returns:
        Tuple of (category, priority):
        - ("nucleus", 3) - High priority Nucleus service, always process
        - ("infra", 1) - Low priority K8s infrastructure, skip unless CRITICAL
        - ("unknown", 2) - Unknown service, process with medium priority
    """
    if not service_name:
        return ("unknown", 2)

    name_lower = service_name.lower()

    # Check Nucleus services first (high priority)
    for svc in NUCLEUS_SERVICES:
        if svc in name_lower:
            return ("nucleus", 3)

    # Check K8s infrastructure (low priority)
    for svc in K8S_INFRA_SERVICES:
        if svc in name_lower:
            return ("infra", 1)

    # Check for kube-system namespace pattern
    if "kube-system" in name_lower or "kube_system" in name_lower:
        return ("infra", 1)

    return ("unknown", 2)


def should_process_service(
    service_name: str,
    error_severity: str = "ERROR",
    resource_type: str = None
) -> Tuple[bool, str]:
    """
    Determine if a service error should create an incident.

    K8s infrastructure errors are skipped unless they are CRITICAL/EMERGENCY.
    Nucleus services always create incidents.
    Unknown services are processed normally.

    Args:
        service_name: The service name
        error_severity: GCP log severity (ERROR, WARNING, CRITICAL, etc.)
        resource_type: Optional GCP resource type

    Returns:
        Tuple of (should_process, reason)
    """
    # Check resource type filter first
    if resource_type and resource_type.lower() in K8S_INFRA_RESOURCE_TYPES:
        if error_severity.upper() in ("CRITICAL", "EMERGENCY", "ALERT"):
            return (True, f"Infrastructure CRITICAL from {resource_type}")
        return (False, f"Filtered K8s infrastructure resource: {resource_type}")

    category, priority = classify_service(service_name)

    if category == "infra":
        # Only process infrastructure if CRITICAL or higher
        if error_severity.upper() in ("CRITICAL", "EMERGENCY", "ALERT"):
            return (True, f"Infrastructure {error_severity} - processing: {service_name}")
        return (False, f"Filtered K8s infrastructure: {service_name}")

    if category == "nucleus":
        return (True, f"Nucleus service: {service_name}")

    return (True, f"Processing unknown service: {service_name}")


def get_service_priority(service_name: str) -> int:
    """
    Get the priority level for a service.

    Args:
        service_name: The service name

    Returns:
        Priority level: 3 (high), 2 (medium), 1 (low)
    """
    _, priority = classify_service(service_name)
    return priority


def is_nucleus_service(service_name: str) -> bool:
    """Check if a service is a known Nucleus MDR service."""
    category, _ = classify_service(service_name)
    return category == "nucleus"


def is_infrastructure_service(service_name: str) -> bool:
    """Check if a service is K8s infrastructure."""
    category, _ = classify_service(service_name)
    return category == "infra"
