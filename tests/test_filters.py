"""
Tests for error filters.

Verifies transient error detection and tenant filtering work correctly.
Patterns are sourced from /Users/sri/oncall/.claude/commands/sre-triage/
"""

import pytest

from backend.filters import (
    is_transient_error,
    TRANSIENT_PATTERNS,
    should_process_tenant,
    is_demo_tenant,
    is_production_tenant,
    get_tenant_info,
    DEMO_TENANTS,
    PRODUCTION_TENANTS,
)


class TestTransientErrorFilter:
    """Tests for transient error detection."""

    # ═══════════════ Network/Connection Patterns ═══════════════

    def test_routing_deadline_expired(self):
        """Test Routing deadline expired is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "Error: Routing deadline expired for request to backend"
        )
        assert is_transient is True
        assert "Cloud Run gRPC" in reason
        assert category == "network"

    def test_timed_out_connecting_to_backend(self):
        """Test AlloyDB timeout is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "Timed out connecting to the backend cluster"
        )
        assert is_transient is True
        assert "AlloyDB" in reason
        assert category == "network"

    def test_context_deadline_exceeded(self):
        """Test context deadline exceeded is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "rpc error: code = DeadlineExceeded desc = context deadline exceeded"
        )
        assert is_transient is True
        assert "external APIs" in reason.lower() or "timeout" in reason.lower()
        assert category == "network"

    def test_http_transport_failure(self):
        """Test HTTP transport failure is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "http_transport_failure: connection refused"
        )
        assert is_transient is True
        assert category == "network"

    def test_connection_reset_by_peer(self):
        """Test connection reset is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "read tcp: connection reset by peer"
        )
        assert is_transient is True
        assert category == "network"

    # ═══════════════ Race Condition Patterns ═══════════════

    def test_case_number_already_exists(self):
        """Test case number race condition is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "Error: case number already exists: CASE-2024-001234"
        )
        assert is_transient is True
        assert "race condition" in reason.lower()
        assert category == "race_condition"

    def test_firestore_duplicate_write(self):
        """Test Firestore duplicate write is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "firestore: BulkWriter received duplicate write for document"
        )
        assert is_transient is True
        assert "idempotency" in reason.lower()
        assert category == "race_condition"

    def test_duplicate_key_case_events(self):
        """Test case_events duplicate key is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "pq: duplicate key value violates unique constraint \"case_events_pkey\""
        )
        assert is_transient is True
        assert category == "race_condition"

    def test_on_conflict_constraint(self):
        """Test ON CONFLICT constraint error is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "ERROR: no unique or exclusion constraint matching the ON CONFLICT specification"
        )
        assert is_transient is True
        assert category == "race_condition"

    # ═══════════════ AI/LLM Patterns ═══════════════

    def test_no_json_in_agent_response(self):
        """Test missing JSON in agent response is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "no JSON found in agent response: got markdown instead"
        )
        assert is_transient is True
        assert category == "ai"

    def test_agent_failed_json_response(self):
        """Test agent JSON failure is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "agent failed to return valid JSON response after 3 attempts"
        )
        assert is_transient is True
        assert "gemini" in reason.lower() or "retries" in reason.lower()
        assert category == "ai"

    def test_case_precedent_agent_failed(self):
        """Test case precedent agent failure is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "case precedent agent call failed: timeout"
        )
        assert is_transient is True
        assert category == "ai"

    # ═══════════════ SOAR Patterns ═══════════════

    def test_soar_secops_id_null(self):
        """Test SOAR with null secops_id is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "SOAR sync error: tenant has secops_id: null"
        )
        assert is_transient is True
        assert category == "soar"

    def test_should_alert_false(self):
        """Test should_alert: false is detected as transient."""
        is_transient, reason, category = is_transient_error(
            'secops error occurred but should_alert: false, skipping notification'
        )
        assert is_transient is True
        assert category == "soar"

    def test_soar_case_not_found(self):
        """Test SOAR case not found is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "SOAR case not found for Tenex case CASE-2024-001234"
        )
        assert is_transient is True
        assert category == "soar"

    # ═══════════════ Rate Limit Patterns ═══════════════

    def test_virustotal_rate_limit(self):
        """Test VirusTotal rate limit is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "VirusTotal API error: rate limit exceeded"
        )
        assert is_transient is True
        assert category == "rate_limit"

    def test_resource_exhausted_quota(self):
        """Test quota exceeded is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "RESOURCE_EXHAUSTED: Quota exceeded for aiplatform.googleapis.com"
        )
        assert is_transient is True
        assert category == "rate_limit"

    # ═══════════════ Non-Blocking Patterns ═══════════════

    def test_tier_one_publish_failed(self):
        """Test tier one publish failure is detected as transient."""
        is_transient, reason, category = is_transient_error(
            "failed to publish tier one message: context canceled"
        )
        assert is_transient is True
        assert category == "non_blocking"

    # ═══════════════ Non-Transient Errors ═══════════════

    def test_null_pointer_not_transient(self):
        """Test NullPointerException is NOT detected as transient."""
        is_transient, reason, category = is_transient_error(
            "panic: runtime error: invalid memory address or nil pointer dereference"
        )
        assert is_transient is False
        assert reason == ""
        assert category is None

    def test_index_out_of_bounds_not_transient(self):
        """Test index out of bounds is NOT detected as transient."""
        is_transient, reason, category = is_transient_error(
            "panic: runtime error: index out of range [5] with length 3"
        )
        assert is_transient is False

    def test_sql_syntax_error_not_transient(self):
        """Test SQL syntax error is NOT detected as transient."""
        is_transient, reason, category = is_transient_error(
            "pq: syntax error at or near \"SELEC\""
        )
        assert is_transient is False

    def test_undefined_variable_not_transient(self):
        """Test undefined variable is NOT detected as transient."""
        is_transient, reason, category = is_transient_error(
            "undefined: someVariable"
        )
        assert is_transient is False

    def test_type_assertion_not_transient(self):
        """Test type assertion failure is NOT detected as transient."""
        is_transient, reason, category = is_transient_error(
            "interface conversion: interface {} is nil, not string"
        )
        assert is_transient is False

    def test_empty_message(self):
        """Test empty message returns not transient."""
        is_transient, reason, category = is_transient_error("")
        assert is_transient is False

    def test_none_message(self):
        """Test None message returns not transient."""
        is_transient, reason, category = is_transient_error(None)
        assert is_transient is False

    # ═══════════════ Case Insensitivity ═══════════════

    def test_case_insensitive_matching(self):
        """Test patterns match case-insensitively."""
        is_transient, _, _ = is_transient_error(
            "ROUTING DEADLINE EXPIRED"
        )
        assert is_transient is True

        is_transient, _, _ = is_transient_error(
            "Context Deadline Exceeded"
        )
        assert is_transient is True


class TestTenantFilter:
    """Tests for tenant filtering."""

    # ═══════════════ Demo Tenant Detection ═══════════════

    def test_tenex_poc_demo_by_id(self):
        """Test TENEX POC Demo is detected as demo by ID."""
        tenant_id = "04d3229f-7097-4af3-86df-37e29775d146"
        assert is_demo_tenant(tenant_id=tenant_id) is True
        assert is_production_tenant(tenant_id=tenant_id) is False

    def test_tenex_demo_by_id(self):
        """Test Tenex Demo is detected as demo by ID."""
        tenant_id = "6af91f8f-8dcb-43c8-9540-c48a9acd0003"
        assert is_demo_tenant(tenant_id=tenant_id) is True

    def test_tenex_internal_by_id(self):
        """Test TENEX Internal is detected as demo by ID."""
        tenant_id = "3a3e2117-cc46-45e8-8092-a963ad9cb6d7"
        assert is_demo_tenant(tenant_id=tenant_id) is True

    def test_tenex_sandbox_by_id(self):
        """Test Tenex Sandbox is detected as demo by ID."""
        tenant_id = "b1545931-5243-4fb4-afb7-a4d92633ffee"
        assert is_demo_tenant(tenant_id=tenant_id) is True

    def test_demo_by_name_pattern_poc(self):
        """Test POC in name is detected as demo."""
        assert is_demo_tenant(tenant_name="Customer POC Environment") is True

    def test_demo_by_name_pattern_demo(self):
        """Test Demo in name is detected as demo."""
        assert is_demo_tenant(tenant_name="Sales Demo Account") is True

    def test_demo_by_name_pattern_test(self):
        """Test Test in name is detected as demo."""
        assert is_demo_tenant(tenant_name="Test Environment") is True

    def test_demo_by_name_pattern_sandbox(self):
        """Test Sandbox in name is detected as demo."""
        assert is_demo_tenant(tenant_name="Development Sandbox") is True

    # ═══════════════ Production Tenant Detection ═══════════════

    def test_whitney_production_by_id(self):
        """Test Whitney is detected as production by ID."""
        tenant_id = "d3f5c2f0-8e11-4ff7-a8cd-a767521ac891"
        assert is_production_tenant(tenant_id=tenant_id) is True
        assert is_demo_tenant(tenant_id=tenant_id) is False

    def test_horizontal_production_by_id(self):
        """Test Horizontal is detected as production by ID."""
        tenant_id = "ceecf0a5-708e-4eb4-9830-7c88c23d949b"
        assert is_production_tenant(tenant_id=tenant_id) is True

    def test_bowtie_production_by_id(self):
        """Test Bowtie is detected as production by ID."""
        tenant_id = "632d61b2-c34e-4c96-95a2-f54d72ef1c3f"
        assert is_production_tenant(tenant_id=tenant_id) is True

    def test_all_production_tenants(self):
        """Test all production tenants are detected correctly."""
        for tenant_id in PRODUCTION_TENANTS:
            assert is_production_tenant(tenant_id=tenant_id) is True
            assert is_demo_tenant(tenant_id=tenant_id) is False

    def test_all_demo_tenants(self):
        """Test all demo tenants are detected correctly."""
        for tenant_id in DEMO_TENANTS:
            assert is_demo_tenant(tenant_id=tenant_id) is True
            assert is_production_tenant(tenant_id=tenant_id) is False

    # ═══════════════ should_process_tenant ═══════════════

    def test_should_not_process_demo_tenant(self):
        """Test demo tenants are not processed."""
        tenant_id = "04d3229f-7097-4af3-86df-37e29775d146"  # TENEX POC Demo
        should_process, reason = should_process_tenant(tenant_id=tenant_id)
        assert should_process is False
        assert "demo" in reason.lower()

    def test_should_process_production_tenant(self):
        """Test production tenants are processed."""
        tenant_id = "d3f5c2f0-8e11-4ff7-a8cd-a767521ac891"  # Whitney
        should_process, reason = should_process_tenant(tenant_id=tenant_id)
        assert should_process is True
        assert "production" in reason.lower()

    def test_should_not_process_demo_name(self):
        """Test demo tenant by name pattern is not processed."""
        should_process, reason = should_process_tenant(tenant_name="Customer POC")
        assert should_process is False
        assert "poc" in reason.lower()

    def test_should_process_unknown_tenant(self):
        """Test unknown tenants are processed with caution."""
        should_process, reason = should_process_tenant(
            tenant_id="unknown-tenant-id-12345"
        )
        assert should_process is True
        assert "unknown" in reason.lower()

    def test_should_process_no_tenant_context(self):
        """Test errors without tenant context are processed."""
        should_process, reason = should_process_tenant()
        assert should_process is True
        assert "no tenant context" in reason.lower()

    # ═══════════════ get_tenant_info ═══════════════

    def test_get_tenant_info_by_id(self):
        """Test getting tenant info by ID."""
        info = get_tenant_info(tenant_id="d3f5c2f0-8e11-4ff7-a8cd-a767521ac891")
        assert info is not None
        assert info.name == "Whitney"
        assert info.is_production is True

    def test_get_tenant_info_by_name(self):
        """Test getting tenant info by name."""
        info = get_tenant_info(tenant_name="Whitney")
        assert info is not None
        assert info.is_production is True

    def test_get_tenant_info_unknown(self):
        """Test getting info for unknown tenant returns None."""
        info = get_tenant_info(tenant_id="unknown-id")
        assert info is None

    # ═══════════════ Edge Cases ═══════════════

    def test_case_insensitive_name_matching(self):
        """Test tenant name matching is case-insensitive."""
        assert is_demo_tenant(tenant_name="DEMO ENVIRONMENT") is True
        assert is_demo_tenant(tenant_name="sandbox account") is True

    def test_partial_name_match(self):
        """Test partial name matching for demo patterns."""
        assert is_demo_tenant(tenant_name="my-staging-env") is True
        assert is_demo_tenant(tenant_name="internal-tools") is True


class TestFilterIntegration:
    """Integration tests combining both filters."""

    def test_demo_tenant_transient_error(self):
        """Test that demo tenant + transient error = definitely skip."""
        # Both filters say skip
        is_transient, _, _ = is_transient_error("Routing deadline expired")
        should_process, _ = should_process_tenant(
            tenant_id="04d3229f-7097-4af3-86df-37e29775d146"
        )

        assert is_transient is True
        assert should_process is False

    def test_production_tenant_real_error(self):
        """Test that production tenant + real error = process."""
        # Real error on production tenant
        is_transient, _, _ = is_transient_error(
            "panic: nil pointer dereference"
        )
        should_process, _ = should_process_tenant(
            tenant_id="d3f5c2f0-8e11-4ff7-a8cd-a767521ac891"  # Whitney
        )

        assert is_transient is False
        assert should_process is True

    def test_production_tenant_transient_error(self):
        """Test that production tenant + transient error = skip."""
        # Transient error on production tenant - still skip
        is_transient, _, _ = is_transient_error("Routing deadline expired")
        should_process, _ = should_process_tenant(
            tenant_id="d3f5c2f0-8e11-4ff7-a8cd-a767521ac891"  # Whitney
        )

        assert is_transient is True
        # Tenant would be processed, but error is transient
        assert should_process is True
