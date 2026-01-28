"""
Tests for Pipeline Orchestrator.

Tests the incident processing pipeline that coordinates triage, fix generation,
code review, sandbox testing, PR creation, and production verification.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.orchestrator import (
    PipelineOrchestrator,
    PipelineStage,
    PipelineEvent,
    PipelineResult,
    EscalationReason,
    process_incident,
)
from backend.models import (
    Incident,
    Severity,
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
from backend.services.github import PullRequest


@pytest.fixture
def sample_incident():
    """Create a sample incident for testing."""
    return Incident(
        id="OCH-TEST0001",
        title="Nil pointer dereference in handler",
        error_message="panic: runtime error: invalid memory address or nil pointer dereference",
        stack_trace="goroutine 1 [running]:\nmain.handler()\n\t/app/handler.go:42",
        file_path="backend/services/caseservice/handler.go",
        service_name="caseservice",
        severity=Severity.HIGH,
        tenant_name="Acme Corp",
        environment="production",
        status=IncidentStatus.ACTIVE,
        created_at=datetime(2024, 1, 15, 10, 30, 0),
    )


@pytest.fixture
def sample_triage_result():
    """Create a sample triage result."""
    return TriageResult(
        incident_id="OCH-TEST0001",
        classification=TriageClassification.FIXABLE,
        root_cause="Nil pointer dereference when case object is None",
        confidence=0.85,
        file_path="backend/services/caseservice/handler.go",
        function_name="processCase",
        code_snippet="case.ID",
        suggested_fix="Add nil check before accessing case.ID",
    )


@pytest.fixture
def sample_fix_result():
    """Create a sample fix result."""
    return FixResult(
        incident_id="OCH-TEST0001",
        file_path="backend/services/caseservice/handler.go",
        original_code="func processCase(case *Case) {\n    id := case.ID\n}",
        fixed_code="func processCase(case *Case) {\n    if case == nil {\n        return\n    }\n    id := case.ID\n}",
        explanation="Added nil check to prevent panic",
        diff_summary="Added nil check for case parameter",
        iteration=1,
    )


@pytest.fixture
def sample_review_passed():
    """Create a passing review result."""
    return ReviewResult(
        passed=True,
        issues=[],
        suggestions=["Consider adding a log statement"],
        summary="",
    )


@pytest.fixture
def sample_review_failed():
    """Create a failing review result."""
    return ReviewResult(
        passed=False,
        issues=[
            ReviewIssue(
                severity="high",
                message="Missing error handling",
                line=5,
                suggestion="Add error return",
            )
        ],
        suggestions=[],
        summary="CodeRabbit found issues: [HIGH] Missing error handling",
    )


@pytest.fixture
def sample_test_result():
    """Create a passing test result."""
    return TestResult(
        incident_id="OCH-TEST0001",
        passed=True,
        unit_tests_passed=True,
        smoke_tests_passed=True,
        tests_run=42,
        tests_passed=42,
        tests_failed=0,
        duration_ms=15000,
    )


@pytest.fixture
def sample_pr():
    """Create a sample pull request."""
    return PullRequest(
        number=123,
        url="https://api.github.com/repos/org/repo/pulls/123",
        html_url="https://github.com/org/repo/pull/123",
        title="[On Call Helper] Fix: Nil pointer dereference",
        body="...",
        state="open",
        head_branch="oncall-helper/fix-test0001",
        base_branch="main",
        draft=True,
        created_at=datetime.utcnow(),
        labels=["oncall-helper", "auto-fix"],
    )


class TestPipelineEvent:
    """Tests for PipelineEvent dataclass."""

    def test_event_creation(self):
        """Test creating a pipeline event."""
        event = PipelineEvent(
            incident_id="OCH-TEST",
            stage=PipelineStage.TRIAGING,
            message="Analyzing incident",
        )

        assert event.incident_id == "OCH-TEST"
        assert event.stage == PipelineStage.TRIAGING
        assert event.message == "Analyzing incident"
        assert event.timestamp is not None

    def test_event_to_dict(self):
        """Test converting event to dictionary."""
        event = PipelineEvent(
            incident_id="OCH-TEST",
            stage=PipelineStage.FIXING,
            message="Generating fix",
            data={"iteration": 1},
        )

        result = event.to_dict()

        assert result["incident_id"] == "OCH-TEST"
        assert result["stage"] == "fixing"
        assert result["data"]["iteration"] == 1


class TestPipelineResult:
    """Tests for PipelineResult dataclass."""

    def test_result_creation(self):
        """Test creating a pipeline result."""
        result = PipelineResult(
            incident_id="OCH-TEST",
            success=True,
            stage_reached=PipelineStage.COMPLETED,
            pr_url="https://github.com/org/repo/pull/123",
        )

        assert result.success is True
        assert result.stage_reached == PipelineStage.COMPLETED
        assert result.pr_url == "https://github.com/org/repo/pull/123"

    def test_result_to_dict(self, sample_triage_result, sample_fix_result):
        """Test converting result to dictionary."""
        result = PipelineResult(
            incident_id="OCH-TEST",
            success=True,
            stage_reached=PipelineStage.COMPLETED,
            triage_result=sample_triage_result,
            fix_result=sample_fix_result,
            duration_seconds=45.5,
        )

        data = result.to_dict()

        assert data["success"] is True
        assert data["stage_reached"] == "completed"
        assert data["triage_result"]["classification"] == "fixable"
        assert data["duration_seconds"] == 45.5


class TestPipelineStage:
    """Tests for PipelineStage enum."""

    def test_stage_values(self):
        """Test stage enum values."""
        assert PipelineStage.RECEIVED.value == "received"
        assert PipelineStage.TRIAGING.value == "triaging"
        assert PipelineStage.FIXING.value == "fixing"
        assert PipelineStage.REVIEWING.value == "reviewing"
        assert PipelineStage.TESTING.value == "testing"
        assert PipelineStage.CREATING_PR.value == "creating_pr"
        assert PipelineStage.VERIFYING.value == "verifying"
        assert PipelineStage.COMPLETED.value == "completed"
        assert PipelineStage.ESCALATED.value == "escalated"
        assert PipelineStage.FAILED.value == "failed"


class TestEscalationReason:
    """Tests for EscalationReason enum."""

    def test_reason_values(self):
        """Test escalation reason values."""
        assert EscalationReason.TRIAGE_FAILED.value == "triage_failed"
        assert EscalationReason.NOT_FIXABLE.value == "not_fixable"
        assert EscalationReason.FIX_GENERATION_FAILED.value == "fix_generation_failed"
        assert EscalationReason.SANDBOX_FAILED.value == "sandbox_failed"


class TestPipelineOrchestratorInit:
    """Tests for PipelineOrchestrator initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default values."""
        orchestrator = PipelineOrchestrator()

        assert orchestrator.triage_agent is None
        assert orchestrator.fixer_agent is None
        assert orchestrator.skip_sandbox is False
        assert orchestrator.skip_verification is False

    def test_init_with_custom_services(self):
        """Test initialization with custom services."""
        mock_triage = MagicMock()
        mock_fixer = MagicMock()
        callback = MagicMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            fixer_agent=mock_fixer,
            event_callback=callback,
            skip_sandbox=True,
        )

        assert orchestrator.triage_agent is mock_triage
        assert orchestrator.fixer_agent is mock_fixer
        assert orchestrator.event_callback is callback
        assert orchestrator.skip_sandbox is True


class TestEventEmission:
    """Tests for event emission."""

    def test_emit_event_calls_callback(self, sample_incident):
        """Test that events are emitted to callback."""
        events = []

        def capture_event(event):
            events.append(event)

        orchestrator = PipelineOrchestrator(event_callback=capture_event)
        orchestrator._emit_event(
            "OCH-TEST",
            PipelineStage.TRIAGING,
            "Test message",
            {"key": "value"},
        )

        assert len(events) == 1
        assert events[0].stage == PipelineStage.TRIAGING
        assert events[0].message == "Test message"
        assert events[0].data["key"] == "value"

    def test_emit_event_handles_callback_error(self):
        """Test that callback errors don't crash the pipeline."""
        def failing_callback(event):
            raise Exception("Callback failed")

        orchestrator = PipelineOrchestrator(event_callback=failing_callback)

        # Should not raise
        orchestrator._emit_event("OCH-TEST", PipelineStage.TRIAGING, "Test")


class TestTriageStage:
    """Tests for the triage stage."""

    @pytest.mark.asyncio
    async def test_run_triage_success(self, sample_incident, sample_triage_result):
        """Test successful triage."""
        mock_agent = AsyncMock()
        mock_agent.analyze.return_value = sample_triage_result

        orchestrator = PipelineOrchestrator(triage_agent=mock_agent)

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator._run_triage(sample_incident)

        assert result.classification == TriageClassification.FIXABLE
        assert result.confidence == 0.85
        mock_agent.analyze.assert_called_once_with(sample_incident)


class TestFixGenerationStage:
    """Tests for the fix generation stage."""

    @pytest.mark.asyncio
    async def test_run_fix_generation_success(
        self, sample_triage_result, sample_fix_result
    ):
        """Test successful fix generation."""
        mock_fixer = AsyncMock()
        mock_fixer.generate_fix.return_value = sample_fix_result

        orchestrator = PipelineOrchestrator(fixer_agent=mock_fixer)

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator._run_fix_generation(sample_triage_result)

        assert result.file_path == sample_fix_result.file_path
        assert "nil check" in result.diff_summary.lower()


class TestReviewStage:
    """Tests for the review stage."""

    @pytest.mark.asyncio
    async def test_run_review_passed(self, sample_fix_result, sample_review_passed):
        """Test review that passes."""
        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = sample_review_passed

        orchestrator = PipelineOrchestrator(coderabbit_service=mock_coderabbit)

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator._run_review(sample_fix_result)

        assert result.passed is True
        assert len(result.issues) == 0

    @pytest.mark.asyncio
    async def test_run_review_failed(self, sample_fix_result, sample_review_failed):
        """Test review that fails."""
        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = sample_review_failed

        orchestrator = PipelineOrchestrator(coderabbit_service=mock_coderabbit)

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator._run_review(sample_fix_result)

        assert result.passed is False
        assert len(result.issues) == 1


class TestSandboxStage:
    """Tests for the sandbox testing stage."""

    @pytest.mark.asyncio
    async def test_run_sandbox_tests_success(
        self, sample_incident, sample_fix_result, sample_test_result
    ):
        """Test successful sandbox tests."""
        mock_sandbox = AsyncMock()
        mock_sandbox.create_sandbox.return_value = MagicMock(
            id="sandbox-123",
            cluster_name="och-test-1234",
        )
        mock_sandbox.run_tests.return_value = sample_test_result

        orchestrator = PipelineOrchestrator(sandbox_service=mock_sandbox)

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator._run_sandbox_tests(
                sample_incident, sample_fix_result
            )

        assert result.passed is True
        assert result.tests_passed == 42
        mock_sandbox.cleanup.assert_called_once()


class TestPRCreationStage:
    """Tests for the PR creation stage."""

    @pytest.mark.asyncio
    async def test_create_pr_success(
        self,
        sample_incident,
        sample_triage_result,
        sample_fix_result,
        sample_test_result,
        sample_pr,
    ):
        """Test successful PR creation."""
        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = sample_pr

        orchestrator = PipelineOrchestrator(github_service=mock_github)

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator._create_pr(
                sample_incident,
                sample_triage_result,
                sample_fix_result,
                sample_test_result,
            )

        assert result == "https://github.com/org/repo/pull/123"


class TestFullPipeline:
    """Tests for the full pipeline flow."""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(
        self,
        sample_incident,
        sample_triage_result,
        sample_fix_result,
        sample_review_passed,
        sample_test_result,
        sample_pr,
    ):
        """Test successful full pipeline run."""
        mock_triage = AsyncMock()
        mock_triage.analyze.return_value = sample_triage_result

        mock_fixer = AsyncMock()
        mock_fixer.generate_fix.return_value = sample_fix_result

        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = sample_review_passed

        mock_sandbox = AsyncMock()
        mock_sandbox.create_sandbox.return_value = MagicMock(
            id="sandbox-123",
            cluster_name="och-test-1234",
        )
        mock_sandbox.run_tests.return_value = sample_test_result

        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = sample_pr
        mock_github.close = AsyncMock()

        mock_pagerduty = AsyncMock()
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            fixer_agent=mock_fixer,
            coderabbit_service=mock_coderabbit,
            sandbox_service=mock_sandbox,
            github_service=mock_github,
            pagerduty_service=mock_pagerduty,
            skip_verification=True,
        )

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator.process_incident(sample_incident)

        assert result.success is True
        assert result.stage_reached == PipelineStage.COMPLETED
        assert result.pr_url == "https://github.com/org/repo/pull/123"
        assert result.triage_result is not None
        assert result.fix_result is not None
        assert result.test_result is not None

    @pytest.mark.asyncio
    async def test_pipeline_with_review_retry(
        self,
        sample_incident,
        sample_triage_result,
        sample_fix_result,
        sample_review_failed,
        sample_review_passed,
        sample_test_result,
        sample_pr,
    ):
        """Test pipeline with review retry loop."""
        mock_triage = AsyncMock()
        mock_triage.analyze.return_value = sample_triage_result

        # Fix returns different results on each call
        fix_iteration_2 = FixResult(
            incident_id="OCH-TEST0001",
            file_path=sample_fix_result.file_path,
            original_code=sample_fix_result.original_code,
            fixed_code="improved code",
            explanation="Fixed with error handling",
            diff_summary="Added error handling",
            iteration=2,
        )

        mock_fixer = AsyncMock()
        mock_fixer.generate_fix.side_effect = [sample_fix_result, fix_iteration_2]

        # Review fails first, then passes
        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.side_effect = [sample_review_failed, sample_review_passed]

        mock_sandbox = AsyncMock()
        mock_sandbox.create_sandbox.return_value = MagicMock(
            id="sandbox-123",
            cluster_name="och-test-1234",
        )
        mock_sandbox.run_tests.return_value = sample_test_result

        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = sample_pr
        mock_github.close = AsyncMock()

        mock_pagerduty = AsyncMock()
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            fixer_agent=mock_fixer,
            coderabbit_service=mock_coderabbit,
            sandbox_service=mock_sandbox,
            github_service=mock_github,
            pagerduty_service=mock_pagerduty,
            skip_verification=True,
        )

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator.process_incident(sample_incident)

        assert result.success is True
        # Should have called generate_fix twice (initial + retry)
        assert mock_fixer.generate_fix.call_count == 2

    @pytest.mark.asyncio
    async def test_pipeline_escalates_non_fixable(self, sample_incident):
        """Test pipeline escalates non-fixable incidents."""
        infra_triage = TriageResult(
            incident_id="OCH-TEST0001",
            classification=TriageClassification.INFRA_ISSUE,
            root_cause="Database connection pool exhausted",
            confidence=0.90,
            runbook_reference="runbooks/alloydb.md",
            manual_steps=["Check connection pool", "Restart service"],
        )

        mock_triage = AsyncMock()
        mock_triage.analyze.return_value = infra_triage

        mock_pagerduty = AsyncMock()
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            pagerduty_service=mock_pagerduty,
        )

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
        assert result.stage_reached == PipelineStage.TRIAGING
        assert result.escalation_reason == EscalationReason.NOT_FIXABLE
        assert "infrastructure" in result.error_message.lower()
        mock_pagerduty.escalate.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_handles_triage_error(self, sample_incident):
        """Test pipeline handles triage errors."""
        from backend.agents.triage import TriageError

        mock_triage = AsyncMock()
        mock_triage.analyze.side_effect = TriageError("API error")

        mock_pagerduty = AsyncMock()
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            pagerduty_service=mock_pagerduty,
        )

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
        assert result.escalation_reason == EscalationReason.TRIAGE_FAILED

    @pytest.mark.asyncio
    async def test_pipeline_handles_sandbox_failure(
        self,
        sample_incident,
        sample_triage_result,
        sample_fix_result,
        sample_review_passed,
    ):
        """Test pipeline handles sandbox test failures."""
        failed_tests = TestResult(
            incident_id="OCH-TEST0001",
            passed=False,
            unit_tests_passed=False,
            tests_run=10,
            tests_passed=7,
            tests_failed=3,
        )

        mock_triage = AsyncMock()
        mock_triage.analyze.return_value = sample_triage_result

        mock_fixer = AsyncMock()
        mock_fixer.generate_fix.return_value = sample_fix_result

        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = sample_review_passed

        mock_sandbox = AsyncMock()
        mock_sandbox.create_sandbox.return_value = MagicMock(
            id="sandbox-123",
            cluster_name="och-test-1234",
        )
        mock_sandbox.run_tests.return_value = failed_tests

        mock_pagerduty = AsyncMock()
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            fixer_agent=mock_fixer,
            coderabbit_service=mock_coderabbit,
            sandbox_service=mock_sandbox,
            pagerduty_service=mock_pagerduty,
        )

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
        assert result.stage_reached == PipelineStage.TESTING
        assert result.escalation_reason == EscalationReason.SANDBOX_FAILED


class TestConcurrencyControl:
    """Tests for concurrent incident handling."""

    @pytest.mark.asyncio
    async def test_rejects_duplicate_incident(self, sample_incident):
        """Test that duplicate incident processing is rejected."""
        import asyncio

        # Create an event to control when triage completes
        triage_started = asyncio.Event()
        triage_can_finish = asyncio.Event()

        async def slow_triage(incident):
            triage_started.set()
            await triage_can_finish.wait()
            return TriageResult(
                incident_id=incident.id,
                classification=TriageClassification.NEEDS_HUMAN,
                root_cause="test",
                confidence=0.5,
            )

        mock_triage = AsyncMock()
        mock_triage.analyze.side_effect = slow_triage

        mock_pagerduty = AsyncMock()
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            pagerduty_service=mock_pagerduty,
        )

        with patch("backend.agents.orchestrator.storage"):
            # Start first processing (will hang at triage)
            task1 = asyncio.create_task(
                orchestrator.process_incident(sample_incident)
            )

            # Wait for triage to start
            await triage_started.wait()

            # Try to process same incident while first is still running
            result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
        assert "already being processed" in result.error_message

        # Cleanup - let first task finish
        triage_can_finish.set()
        try:
            await asyncio.wait_for(task1, timeout=1.0)
        except asyncio.TimeoutError:
            task1.cancel()
            try:
                await task1
            except asyncio.CancelledError:
                pass

    def test_is_processing(self, sample_incident):
        """Test checking if incident is being processed."""
        orchestrator = PipelineOrchestrator()

        assert orchestrator.is_processing("OCH-TEST") is False


class TestSkipFlags:
    """Tests for skip flags."""

    @pytest.mark.asyncio
    async def test_skip_sandbox(
        self,
        sample_incident,
        sample_triage_result,
        sample_fix_result,
        sample_review_passed,
        sample_pr,
    ):
        """Test pipeline with sandbox skipped."""
        mock_triage = AsyncMock()
        mock_triage.analyze.return_value = sample_triage_result

        mock_fixer = AsyncMock()
        mock_fixer.generate_fix.return_value = sample_fix_result

        mock_coderabbit = AsyncMock()
        mock_coderabbit.review.return_value = sample_review_passed

        mock_github = AsyncMock()
        mock_github.create_fix_pr.return_value = sample_pr
        mock_github.close = AsyncMock()

        mock_pagerduty = AsyncMock()
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            fixer_agent=mock_fixer,
            coderabbit_service=mock_coderabbit,
            github_service=mock_github,
            pagerduty_service=mock_pagerduty,
            skip_sandbox=True,  # Skip sandbox
            skip_verification=True,
        )

        with patch("backend.agents.orchestrator.storage"):
            result = await orchestrator.process_incident(sample_incident)

        assert result.success is True
        assert result.test_result is None  # No tests run


class TestModuleFunction:
    """Tests for module-level convenience function."""

    @pytest.mark.asyncio
    async def test_process_incident_function(self, sample_incident):
        """Test process_incident convenience function."""
        with patch("backend.agents.orchestrator.PipelineOrchestrator") as mock_class:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.process_incident.return_value = PipelineResult(
                incident_id="OCH-TEST",
                success=True,
                stage_reached=PipelineStage.COMPLETED,
            )
            mock_class.return_value = mock_orchestrator

            result = await process_incident(sample_incident)

            assert result.success is True
            mock_orchestrator.process_incident.assert_called_once()


class TestEscalationMessages:
    """Tests for escalation message generation."""

    def test_infra_issue_message(self):
        """Test escalation message for infrastructure issue."""
        triage = TriageResult(
            incident_id="OCH-TEST",
            classification=TriageClassification.INFRA_ISSUE,
            root_cause="Database overload",
            confidence=0.9,
            runbook_reference="runbooks/alloydb.md",
            manual_steps=["Check metrics", "Scale up"],
        )

        orchestrator = PipelineOrchestrator()
        message = orchestrator._get_escalation_message_for_classification(triage)

        assert "infrastructure" in message.lower()
        assert "runbooks/alloydb.md" in message
        assert "Check metrics" in message

    def test_transient_error_message(self):
        """Test escalation message for transient error."""
        triage = TriageResult(
            incident_id="OCH-TEST",
            classification=TriageClassification.TRANSIENT,
            root_cause="Temporary network glitch",
            confidence=0.95,
        )

        orchestrator = PipelineOrchestrator()
        message = orchestrator._get_escalation_message_for_classification(triage)

        assert "transient" in message.lower()
        assert "self-healing" in message.lower()

    def test_needs_human_message(self):
        """Test escalation message for needs human."""
        triage = TriageResult(
            incident_id="OCH-TEST",
            classification=TriageClassification.NEEDS_HUMAN,
            root_cause="Complex race condition requiring investigation",
            confidence=0.6,
        )

        orchestrator = PipelineOrchestrator()
        message = orchestrator._get_escalation_message_for_classification(triage)

        assert "human" in message.lower()
        assert "investigation" in message.lower()


class TestPagerDutyNotifications:
    """Tests for PagerDuty notification handling."""

    @pytest.mark.asyncio
    async def test_pagerduty_trigger_called(self, sample_incident, sample_triage_result):
        """Test that PagerDuty trigger is called."""
        mock_triage = AsyncMock()
        mock_triage.analyze.return_value = sample_triage_result

        mock_pagerduty = AsyncMock()
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            pagerduty_service=mock_pagerduty,
            skip_sandbox=True,
            skip_verification=True,
        )

        # Make fixer fail to stop pipeline early
        mock_fixer = AsyncMock()
        mock_fixer.generate_fix.side_effect = Exception("test")
        orchestrator.fixer_agent = mock_fixer

        with patch("backend.agents.orchestrator.storage"):
            await orchestrator.process_incident(sample_incident)

        mock_pagerduty.trigger.assert_called_once()

    @pytest.mark.asyncio
    async def test_pagerduty_error_doesnt_crash_pipeline(self, sample_incident):
        """Test that PagerDuty errors don't crash the pipeline."""
        mock_triage = AsyncMock()
        mock_triage.analyze.side_effect = Exception("triage failed")

        mock_pagerduty = AsyncMock()
        mock_pagerduty.trigger.side_effect = Exception("PagerDuty error")
        mock_pagerduty.escalate.side_effect = Exception("PagerDuty error")
        mock_pagerduty.close = AsyncMock()

        orchestrator = PipelineOrchestrator(
            triage_agent=mock_triage,
            pagerduty_service=mock_pagerduty,
        )

        with patch("backend.agents.orchestrator.storage"):
            # Should not raise
            result = await orchestrator.process_incident(sample_incident)

        assert result.success is False
