"""
End-to-end tests for the full incident processing pipeline.

Tests the complete flow from error ingestion through to PR creation,
with mocked external services.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models import (
    Incident,
    IncidentStatus,
    TriageResult,
    TriageClassification,
    FixResult,
    ReviewResult,
    ReviewIssue,
    TestResult,
    VerificationResult,
    VerificationStatus,
)
from backend.agents.orchestrator import (
    PipelineOrchestrator,
    PipelineStage,
    PipelineResult,
    EscalationReason,
)
from backend.storage import storage


@pytest.fixture
def sample_incident():
    """Create a sample incident for testing."""
    return Incident(
        id="INC-E2E-001",
        title="NullPointerException in caseservice",
        error_message="panic: runtime error: invalid memory address",
        stack_trace="""goroutine 1 [running]:
main.processCase(0xc0001a4000)
    /backend/services/caseservice/handler.go:142 +0x234
main.main()
    /backend/services/caseservice/main.go:28 +0x1a4""",
        service_name="caseservice",
        severity="high",
        source="gcp-logging",
        status=IncidentStatus.ACTIVE,
        created_at=datetime.utcnow(),
    )


@pytest.fixture
def mock_triage_result():
    """Create a mock triage result."""
    return TriageResult(
        incident_id="INC-E2E-001",
        classification=TriageClassification.FIXABLE,
        confidence=0.92,
        root_cause="Nil pointer dereference when case object is not found",
        suggested_fix="Add nil check before accessing case fields",
        file_path="services/caseservice/handler.go",
        affected_lines=[142, 145],
        runbook_reference=None,
        manual_steps=[],
    )


@pytest.fixture
def mock_fix_result():
    """Create a mock fix result."""
    return FixResult(
        incident_id="INC-E2E-001",
        file_path="services/caseservice/handler.go",
        original_code="func processCase(c *Case) {\n    c.Process()",
        fixed_code="func processCase(c *Case) {\n    if c == nil {\n        return\n    }\n    c.Process()",
        explanation="Added nil check to prevent panic when case is not found",
        diff_summary="+3 -1 lines",
        confidence=0.88,
        iteration=1,
    )


@pytest.fixture
def mock_review_result_pass():
    """Create a passing mock review result."""
    return ReviewResult(
        passed=True,
        issues=[],
        suggestions=["Consider adding a log message when case is nil"],
        summary="Code looks good. The nil check properly guards against the panic.",
    )


@pytest.fixture
def mock_review_result_fail():
    """Create a failing mock review result."""
    return ReviewResult(
        passed=False,
        issues=[
            ReviewIssue(
                severity="warning",
                message="Silently returning on nil may hide errors",
                line=3,
                suggestion="Consider returning an error instead",
            )
        ],
        suggestions=[],
        summary="The fix may hide errors by silently returning",
    )


@pytest.fixture
def mock_test_result_pass():
    """Create a passing mock test result."""
    return TestResult(
        incident_id="INC-E2E-001",
        passed=True,
        tests_run=42,
        tests_passed=42,
        tests_failed=0,
        duration_seconds=23.5,
        output="All tests passed",
        failed_tests=[],
    )


@pytest.fixture
def mock_test_result_fail():
    """Create a failing mock test result."""
    return TestResult(
        incident_id="INC-E2E-001",
        passed=False,
        tests_run=42,
        tests_passed=40,
        tests_failed=2,
        duration_seconds=25.3,
        output="2 tests failed",
        failed_tests=["TestProcessCase_NilInput", "TestProcessCase_EmptyCase"],
    )


@pytest.fixture
def mock_pr():
    """Create a mock PR response."""
    mock = MagicMock()
    mock.number = 123
    mock.html_url = "https://github.com/org/nucleus/pull/123"
    return mock


@pytest.fixture
def mock_verification_result():
    """Create a mock verification result."""
    return VerificationResult(
        incident_id="INC-E2E-001",
        status=VerificationStatus.SUCCESS,
        errors_before=150,
        errors_after=0,
        samples_taken=6,
        duration_seconds=7200,
        message="Error rate dropped to 0 after fix",
        pr_url="https://github.com/org/nucleus/pull/123",
    )


class TestFullPipelineSuccess:
    """Test successful end-to-end pipeline execution."""

    @pytest.mark.asyncio
    async def test_complete_pipeline_success(
        self,
        sample_incident,
        mock_triage_result,
        mock_fix_result,
        mock_review_result_pass,
        mock_test_result_pass,
        mock_pr,
        mock_verification_result,
    ):
        """Test complete pipeline from ingestion to verified fix."""
        # Setup mocks
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = mock_triage_result

        mock_fixer_agent = AsyncMock()
        mock_fixer_agent.generate_fix.return_value = mock_fix_result

        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = mock_review_result_pass

        mock_sandbox = AsyncMock()
        mock_sandbox.create_sandbox.return_value = MagicMock(
            id="sandbox-001",
            cluster_name="test-cluster",
        )
        mock_sandbox.run_tests.return_value = mock_test_result_pass

        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = mock_pr

        mock_production_monitor = AsyncMock()
        mock_production_monitor.verify_fix.return_value = mock_verification_result

        mock_pagerduty = AsyncMock()

        # Track events
        events = []

        def event_callback(event):
            events.append(event)

        # Create orchestrator
        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            fixer_agent=mock_fixer_agent,
            coderabbit_service=mock_coderabbit,
            sandbox_service=mock_sandbox,
            github_service=mock_github,
            pagerduty_service=mock_pagerduty,
            production_monitor=mock_production_monitor,
            event_callback=event_callback,
        )

        # Store incident
        storage.save_incident(sample_incident)

        # Process
        result = await orchestrator.process_incident(
            sample_incident,
            pr_merged_at=datetime.utcnow(),
        )

        # Verify success
        assert result.success is True
        assert result.stage_reached == PipelineStage.COMPLETED
        assert result.triage_result is not None
        assert result.fix_result is not None
        assert result.review_result is not None
        assert result.test_result is not None
        assert result.pr_url == mock_pr.html_url
        assert result.verification_result is not None

        # Verify all stages were called
        mock_triage_agent.analyze.assert_called_once()
        mock_fixer_agent.generate_fix.assert_called_once()
        mock_coderabbit.review.assert_called_once()
        mock_sandbox.create_sandbox.assert_called_once()
        mock_sandbox.run_tests.assert_called_once()
        mock_github.create_fix_pr.assert_called_once()
        mock_production_monitor.verify_fix.assert_called_once()

        # Verify PagerDuty notifications
        assert mock_pagerduty.trigger.called
        assert mock_pagerduty.acknowledge.called
        assert mock_pagerduty.resolve.called

        # Verify events were emitted
        assert len(events) > 0
        stages = [e.stage for e in events]
        assert PipelineStage.RECEIVED in stages
        assert PipelineStage.TRIAGING in stages
        assert PipelineStage.FIXING in stages
        assert PipelineStage.REVIEWING in stages
        assert PipelineStage.TESTING in stages
        assert PipelineStage.CREATING_PR in stages
        assert PipelineStage.COMPLETED in stages

    @pytest.mark.asyncio
    async def test_pipeline_without_sandbox_and_verification(
        self,
        sample_incident,
        mock_triage_result,
        mock_fix_result,
        mock_review_result_pass,
        mock_pr,
    ):
        """Test pipeline with sandbox and verification skipped."""
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = mock_triage_result

        mock_fixer_agent = AsyncMock()
        mock_fixer_agent.generate_fix.return_value = mock_fix_result

        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = mock_review_result_pass

        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = mock_pr

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            fixer_agent=mock_fixer_agent,
            coderabbit_service=mock_coderabbit,
            github_service=mock_github,
            skip_sandbox=True,
            skip_verification=True,
        )

        storage.save_incident(sample_incident)
        result = await orchestrator.process_incident(sample_incident)

        assert result.success is True
        assert result.test_result is None
        assert result.verification_result is None
        assert result.pr_url == mock_pr.html_url


class TestPipelineCodeRabbitLoop:
    """Test CodeRabbit review iteration loop."""

    @pytest.mark.asyncio
    async def test_review_retry_on_failure(
        self,
        sample_incident,
        mock_triage_result,
        mock_fix_result,
        mock_review_result_fail,
        mock_review_result_pass,
        mock_test_result_pass,
        mock_pr,
    ):
        """Test that failed reviews trigger fix regeneration."""
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = mock_triage_result

        # Track fix iterations
        fix_iterations = []
        mock_fixer_agent = AsyncMock()

        async def generate_fix_side_effect(triage, coderabbit_feedback=None, previous_fix=None):
            iteration = 1 if previous_fix is None else previous_fix.iteration + 1
            fix_iterations.append(iteration)
            result = FixResult(
                incident_id=triage.incident_id,
                file_path="services/caseservice/handler.go",
                original_code="original",
                fixed_code=f"fixed_v{iteration}",
                explanation="fix",
                diff_summary="+1 -1",
                confidence=0.9,
                iteration=iteration,
            )
            return result

        mock_fixer_agent.generate_fix.side_effect = generate_fix_side_effect

        # First review fails, second passes
        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.side_effect = [
            mock_review_result_fail,
            mock_review_result_pass,
        ]

        mock_sandbox = AsyncMock()
        mock_sandbox.create_sandbox.return_value = MagicMock(id="sb-1", cluster_name="test")
        mock_sandbox.run_tests.return_value = mock_test_result_pass

        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = mock_pr

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            fixer_agent=mock_fixer_agent,
            coderabbit_service=mock_coderabbit,
            sandbox_service=mock_sandbox,
            github_service=mock_github,
            skip_verification=True,
        )

        storage.save_incident(sample_incident)
        result = await orchestrator.process_incident(sample_incident)

        assert result.success is True
        assert len(fix_iterations) == 2  # Two fix attempts
        assert mock_coderabbit.review.call_count == 2

    @pytest.mark.asyncio
    async def test_escalates_after_max_review_iterations(
        self,
        sample_incident,
        mock_triage_result,
        mock_fix_result,
        mock_review_result_fail,
    ):
        """Test escalation when max review iterations exceeded."""
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = mock_triage_result

        mock_fixer_agent = AsyncMock()
        mock_fixer_agent.generate_fix.return_value = mock_fix_result

        # Always fail review
        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = mock_review_result_fail

        mock_pagerduty = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            fixer_agent=mock_fixer_agent,
            coderabbit_service=mock_coderabbit,
            pagerduty_service=mock_pagerduty,
            skip_sandbox=True,
            skip_verification=True,
        )

        storage.save_incident(sample_incident)
        result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
        assert result.stage_reached == PipelineStage.REVIEWING
        assert result.escalation_reason == EscalationReason.REVIEW_FAILED_MAX_RETRIES
        assert mock_coderabbit.review.call_count == 3  # Max iterations


class TestPipelineEscalation:
    """Test various escalation scenarios."""

    @pytest.mark.asyncio
    async def test_escalates_on_infra_issue(self, sample_incident):
        """Test escalation when triage identifies infra issue."""
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = TriageResult(
            incident_id=sample_incident.id,
            classification=TriageClassification.INFRA_ISSUE,
            confidence=0.95,
            root_cause="Database connection pool exhausted",
            suggested_fix=None,
            file_path=None,
            affected_lines=[],
            runbook_reference="runbooks/db-connection-pool.md",
            manual_steps=["Check connection pool metrics", "Restart service"],
        )

        mock_pagerduty = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            pagerduty_service=mock_pagerduty,
        )

        storage.save_incident(sample_incident)
        result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
        assert result.escalation_reason == EscalationReason.NOT_FIXABLE
        assert "Infrastructure issue" in result.error_message

    @pytest.mark.asyncio
    async def test_escalates_on_needs_human(self, sample_incident):
        """Test escalation when triage identifies human-required issue."""
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = TriageResult(
            incident_id=sample_incident.id,
            classification=TriageClassification.NEEDS_HUMAN,
            confidence=0.85,
            root_cause="Complex business logic error requiring domain knowledge",
            suggested_fix=None,
            file_path=None,
            affected_lines=[],
            runbook_reference=None,
            manual_steps=[],
        )

        mock_pagerduty = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            pagerduty_service=mock_pagerduty,
        )

        storage.save_incident(sample_incident)
        result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
        assert result.escalation_reason == EscalationReason.NOT_FIXABLE

    @pytest.mark.asyncio
    async def test_escalates_on_sandbox_failure(
        self,
        sample_incident,
        mock_triage_result,
        mock_fix_result,
        mock_review_result_pass,
        mock_test_result_fail,
    ):
        """Test escalation when sandbox tests fail."""
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = mock_triage_result

        mock_fixer_agent = AsyncMock()
        mock_fixer_agent.generate_fix.return_value = mock_fix_result

        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = mock_review_result_pass

        mock_sandbox = AsyncMock()
        mock_sandbox.create_sandbox.return_value = MagicMock(id="sb-1", cluster_name="test")
        mock_sandbox.run_tests.return_value = mock_test_result_fail

        mock_pagerduty = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            fixer_agent=mock_fixer_agent,
            coderabbit_service=mock_coderabbit,
            sandbox_service=mock_sandbox,
            pagerduty_service=mock_pagerduty,
            skip_verification=True,
        )

        storage.save_incident(sample_incident)
        result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
        assert result.escalation_reason == EscalationReason.SANDBOX_FAILED
        assert result.test_result.tests_failed == 2


class TestPipelineWebhookToCompletion:
    """Test complete flow from webhook to completion."""

    @pytest.mark.asyncio
    async def test_webhook_triggers_pipeline(self, sample_incident):
        """Test that webhook ingestion can trigger pipeline processing."""
        # Create an incident and save it (simulating webhook ingestion)
        storage.save_incident(sample_incident)

        # Verify incident was stored correctly
        retrieved = storage.get_incident(sample_incident.id)
        assert retrieved is not None
        assert retrieved.id == sample_incident.id
        assert retrieved.service_name == "caseservice"
        assert retrieved.status == IncidentStatus.ACTIVE

        # Verify we can update it (simulating pipeline processing)
        retrieved.status = IncidentStatus.TRIAGING
        storage.save_incident(retrieved)

        updated = storage.get_incident(sample_incident.id)
        assert updated.status == IncidentStatus.TRIAGING

    @pytest.mark.asyncio
    async def test_concurrent_incident_rejection(self, sample_incident):
        """Test that duplicate incident processing is rejected."""
        import asyncio

        mock_triage_agent = AsyncMock()

        # Make triage slow so we can test concurrency
        async def slow_triage(incident):
            await asyncio.sleep(0.1)
            return TriageResult(
                incident_id=incident.id,
                classification=TriageClassification.TRANSIENT,
                confidence=0.9,
                root_cause="test",
                suggested_fix=None,
                file_path=None,
                affected_lines=[],
            )

        mock_triage_agent.analyze.side_effect = slow_triage

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            skip_sandbox=True,
            skip_verification=True,
        )

        storage.save_incident(sample_incident)

        # Start first processing (don't await)
        task1 = asyncio.create_task(orchestrator.process_incident(sample_incident))

        # Small delay to let task1 start
        await asyncio.sleep(0.01)

        # Try to start second processing
        result2 = await orchestrator.process_incident(sample_incident)

        # Second should be rejected
        assert result2.success is False
        assert "already being processed" in result2.error_message

        # Wait for first to complete
        result1 = await task1
        # First should complete (even if escalated due to TRANSIENT classification)


class TestPipelineMetrics:
    """Test pipeline metrics and timing."""

    @pytest.mark.asyncio
    async def test_pipeline_tracks_duration(
        self,
        sample_incident,
        mock_triage_result,
        mock_fix_result,
        mock_review_result_pass,
        mock_pr,
    ):
        """Test that pipeline tracks execution duration."""
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = mock_triage_result

        mock_fixer_agent = AsyncMock()
        mock_fixer_agent.generate_fix.return_value = mock_fix_result

        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = mock_review_result_pass

        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = mock_pr

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            fixer_agent=mock_fixer_agent,
            coderabbit_service=mock_coderabbit,
            github_service=mock_github,
            skip_sandbox=True,
            skip_verification=True,
        )

        storage.save_incident(sample_incident)
        result = await orchestrator.process_incident(sample_incident)

        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_pipeline_result_serialization(
        self,
        sample_incident,
        mock_triage_result,
        mock_fix_result,
        mock_review_result_pass,
        mock_pr,
    ):
        """Test that pipeline result can be serialized."""
        mock_triage_agent = AsyncMock()
        mock_triage_agent.analyze.return_value = mock_triage_result

        mock_fixer_agent = AsyncMock()
        mock_fixer_agent.generate_fix.return_value = mock_fix_result

        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = mock_review_result_pass

        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = mock_pr

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage_agent,
            fixer_agent=mock_fixer_agent,
            coderabbit_service=mock_coderabbit,
            github_service=mock_github,
            skip_sandbox=True,
            skip_verification=True,
        )

        storage.save_incident(sample_incident)
        result = await orchestrator.process_incident(sample_incident)

        # Serialize to dict
        result_dict = result.to_dict()

        assert isinstance(result_dict, dict)
        assert result_dict["incident_id"] == sample_incident.id
        assert result_dict["success"] is True
        assert "triage_result" in result_dict
        assert "fix_result" in result_dict
