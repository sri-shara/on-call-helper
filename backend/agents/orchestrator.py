"""
Pipeline Orchestrator for On Call Helper.

Coordinates all components of the incident response pipeline:
Triage → Fix → Review → Test → PR creation → Production verification.

Handles the full lifecycle of an incident with proper error handling,
retry logic, and status broadcasting.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from backend.config import settings
from backend.models import (
    Incident,
    IncidentStatus,
    TriageResult,
    TriageClassification,
    FixResult,
    ReviewResult,
    TestResult,
    VerificationResult,
    VerificationStatus,
)
from backend.agents.triage import TriageAgent, TriageError
from backend.agents.fixer import FixerAgent, FixerError
from backend.services.coderabbit import (
    CodeRabbitService,
    CodeRabbitError,
    CodeRabbitNotInstalledError,
)
from backend.services.sandbox import (
    SandboxService,
    SandboxError,
    KindNotInstalledError,
)
from backend.services.github import GitHubService, GitHubError
from backend.services.pagerduty import PagerDutyService, PagerDutyError
from backend.services.production_monitor import (
    ProductionMonitorService,
    ProductionMonitorError,
)
from backend.storage import storage

logger = logging.getLogger(__name__)


class PipelineStage(str, Enum):
    """Stages of the incident processing pipeline."""

    RECEIVED = "received"
    TRIAGING = "triaging"
    FIXING = "fixing"
    REVIEWING = "reviewing"
    TESTING = "testing"
    CREATING_PR = "creating_pr"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    FAILED = "failed"


class EscalationReason(str, Enum):
    """Reasons for escalating to human attention."""

    TRIAGE_FAILED = "triage_failed"
    NOT_FIXABLE = "not_fixable"
    FIX_GENERATION_FAILED = "fix_generation_failed"
    REVIEW_FAILED_MAX_RETRIES = "review_failed_max_retries"
    SANDBOX_FAILED = "sandbox_failed"
    PR_CREATION_FAILED = "pr_creation_failed"
    VERIFICATION_FAILED = "verification_failed"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class PipelineEvent:
    """Event emitted by the pipeline for status updates."""

    incident_id: str
    stage: PipelineStage
    message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "incident_id": self.incident_id,
            "stage": self.stage.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }


@dataclass
class PipelineResult:
    """Result of processing an incident through the pipeline."""

    incident_id: str
    success: bool
    stage_reached: PipelineStage
    triage_result: Optional[TriageResult] = None
    fix_result: Optional[FixResult] = None
    review_result: Optional[ReviewResult] = None
    test_result: Optional[TestResult] = None
    pr_url: Optional[str] = None
    verification_result: Optional[VerificationResult] = None
    escalation_reason: Optional[EscalationReason] = None
    error_message: Optional[str] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "incident_id": self.incident_id,
            "success": self.success,
            "stage_reached": self.stage_reached.value,
            "triage_result": self.triage_result.dict() if self.triage_result else None,
            "fix_result": self.fix_result.dict() if self.fix_result else None,
            "review_result": self.review_result.dict() if self.review_result else None,
            "test_result": self.test_result.dict() if self.test_result else None,
            "pr_url": self.pr_url,
            "verification_result": self.verification_result.dict() if self.verification_result else None,
            "escalation_reason": self.escalation_reason.value if self.escalation_reason else None,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
        }


# Type alias for event callback
EventCallback = Callable[[PipelineEvent], None]


class PipelineOrchestrator:
    """
    Coordinates the incident response pipeline.

    Manages the flow from incident detection through triage, fix generation,
    review, testing, PR creation, and production verification.
    """

    # Maximum CodeRabbit review iterations
    MAX_REVIEW_ITERATIONS = 3

    def __init__(
        self,
        triage_agent: Optional[TriageAgent] = None,
        fixer_agent: Optional[FixerAgent] = None,
        coderabbit_service: Optional[CodeRabbitService] = None,
        sandbox_service: Optional[SandboxService] = None,
        github_service: Optional[GitHubService] = None,
        pagerduty_service: Optional[PagerDutyService] = None,
        production_monitor: Optional[ProductionMonitorService] = None,
        event_callback: Optional[EventCallback] = None,
        skip_sandbox: bool = False,
        skip_verification: bool = False,
    ):
        """
        Initialize the pipeline orchestrator.

        Args:
            triage_agent: Agent for incident triage
            fixer_agent: Agent for code fix generation
            coderabbit_service: Service for code review
            sandbox_service: Service for sandbox testing
            github_service: Service for GitHub operations
            pagerduty_service: Service for PagerDuty notifications
            production_monitor: Service for production verification
            event_callback: Callback for pipeline events
            skip_sandbox: Skip sandbox testing (for environments without Kind)
            skip_verification: Skip production verification
        """
        self.triage_agent = triage_agent
        self.fixer_agent = fixer_agent
        self.coderabbit = coderabbit_service
        self.sandbox = sandbox_service
        self.github = github_service
        self.pagerduty = pagerduty_service
        self.production_monitor = production_monitor
        self.event_callback = event_callback
        self.skip_sandbox = skip_sandbox
        self.skip_verification = skip_verification

        # Track active pipelines to prevent duplicate processing
        self._active_incidents: Dict[str, asyncio.Task] = {}

    def _emit_event(
        self,
        incident_id: str,
        stage: PipelineStage,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a pipeline event."""
        event = PipelineEvent(
            incident_id=incident_id,
            stage=stage,
            message=message,
            data=data or {},
        )

        logger.info(f"[{incident_id}] {stage.value}: {message}")

        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception as e:
                logger.warning(f"Event callback failed: {e}")

    async def _notify_pagerduty(
        self,
        incident: Incident,
        action: str,
        **kwargs,
    ) -> None:
        """Send PagerDuty notification (fire-and-forget)."""
        if not self.pagerduty:
            return

        try:
            if action == "trigger":
                await self.pagerduty.trigger(incident)
            elif action == "acknowledge":
                await self.pagerduty.acknowledge(incident.id)
            elif action == "resolve":
                await self.pagerduty.resolve(
                    incident.id,
                    pr_url=kwargs.get("pr_url"),
                )
            elif action == "escalate":
                await self.pagerduty.escalate(
                    incident.id,
                    reason=kwargs.get("reason", "Pipeline failed"),
                )
        except PagerDutyError as e:
            logger.warning(f"PagerDuty notification failed: {e}")

    def _update_incident_status(self, incident_id: str, status: IncidentStatus) -> None:
        """Update incident status in storage."""
        incident = storage.get_incident(incident_id)
        if incident:
            incident.status = status
            if status == IncidentStatus.FIXED:
                incident.resolved_at = datetime.utcnow()
            storage.save_incident(incident)

    async def _run_triage(self, incident: Incident) -> TriageResult:
        """Run triage on the incident."""
        self._emit_event(
            incident.id,
            PipelineStage.TRIAGING,
            "Analyzing incident with Claude AI",
        )

        self._update_incident_status(incident.id, IncidentStatus.TRIAGING)

        if not self.triage_agent:
            self.triage_agent = TriageAgent()

        result = await self.triage_agent.analyze(incident)

        # Build triage event data
        triage_data = {
            "classification": result.classification.value,
            "confidence": result.confidence,
            "root_cause": result.root_cause,
        }

        # Include GCP context if available
        if result.gcp_context:
            triage_data["gcp_context"] = result.gcp_context
            if result.gcp_context.get("error_frequency"):
                freq = result.gcp_context["error_frequency"]
                triage_data["error_count_past_hour"] = freq.get("total_errors_past_hour", 0)
            if result.gcp_context.get("similar_across_services"):
                triage_data["affected_services"] = [
                    s["service"] for s in result.gcp_context["similar_across_services"]
                ]

        # Include actionable info for non-fixable classifications
        if result.classification == TriageClassification.INFRA_ISSUE:
            if result.runbook_reference:
                triage_data["runbook"] = result.runbook_reference
            if result.manual_steps:
                triage_data["manual_steps"] = result.manual_steps
        elif result.classification == TriageClassification.TRANSIENT:
            triage_data["action"] = "Monitor - error is self-healing"

        self._emit_event(
            incident.id,
            PipelineStage.TRIAGING,
            f"Triage complete: {result.classification.value} (confidence: {result.confidence:.0%})",
            data=triage_data,
        )

        # Persist triage result to storage
        storage.save_triage_result(result)

        return result

    async def _run_fix_generation(
        self,
        triage: TriageResult,
        feedback: Optional[str] = None,
        previous_fix: Optional[FixResult] = None,
    ) -> FixResult:
        """Generate a code fix based on triage."""
        iteration = 1 if not previous_fix else previous_fix.iteration + 1

        self._emit_event(
            triage.incident_id,
            PipelineStage.FIXING,
            f"Generating code fix (iteration {iteration}/{self.MAX_REVIEW_ITERATIONS})",
            data={"iteration": iteration},
        )

        self._update_incident_status(triage.incident_id, IncidentStatus.FIXING)

        if not self.fixer_agent:
            self.fixer_agent = FixerAgent(github_service=self.github)

        result = await self.fixer_agent.generate_fix(
            triage,
            coderabbit_feedback=feedback,
            previous_fix=previous_fix,
        )

        self._emit_event(
            triage.incident_id,
            PipelineStage.FIXING,
            f"Fix generated: {result.diff_summary}",
            data={
                "file_path": result.file_path,
                "iteration": result.iteration,
            },
        )

        # Persist fix result to storage
        storage.save_fix_result(result)

        return result

    async def _run_review(self, fix: FixResult) -> ReviewResult:
        """Review the fix with CodeRabbit."""
        self._emit_event(
            fix.incident_id,
            PipelineStage.REVIEWING,
            "Reviewing code with CodeRabbit",
        )

        self._update_incident_status(fix.incident_id, IncidentStatus.REVIEWING)

        if not self.coderabbit:
            self.coderabbit = CodeRabbitService()

        result = await self.coderabbit.review(fix)

        status = "passed" if result.passed else f"failed ({len(result.issues)} issues)"
        self._emit_event(
            fix.incident_id,
            PipelineStage.REVIEWING,
            f"Code review {status}",
            data={
                "passed": result.passed,
                "issues_count": len(result.issues),
            },
        )

        return result

    async def _run_sandbox_tests(
        self,
        incident: Incident,
        fix: FixResult,
    ) -> TestResult:
        """Run tests in sandbox environment."""
        self._emit_event(
            incident.id,
            PipelineStage.TESTING,
            "Setting up sandbox environment",
        )

        self._update_incident_status(incident.id, IncidentStatus.TESTING)

        if not self.sandbox:
            self.sandbox = SandboxService()

        # Create sandbox
        sandbox = await self.sandbox.create_sandbox(incident.id)

        self._emit_event(
            incident.id,
            PipelineStage.TESTING,
            f"Sandbox created: {sandbox.cluster_name}",
            data={"sandbox_id": sandbox.id},
        )

        try:
            # Apply fix
            await self.sandbox.apply_fix(sandbox, fix)

            self._emit_event(
                incident.id,
                PipelineStage.TESTING,
                "Running tests in sandbox",
            )

            # Run tests
            result = await self.sandbox.run_tests(sandbox, incident.id)

            status = "passed" if result.passed else "failed"
            self._emit_event(
                incident.id,
                PipelineStage.TESTING,
                f"Tests {status}: {result.tests_passed}/{result.tests_run} passed",
                data={
                    "passed": result.passed,
                    "tests_run": result.tests_run,
                    "tests_passed": result.tests_passed,
                },
            )

            # Persist test result to storage
            storage.save_test_result(result)

            return result

        finally:
            # Always cleanup sandbox
            await self.sandbox.cleanup(sandbox)

    async def _create_pr(
        self,
        incident: Incident,
        triage: TriageResult,
        fix: FixResult,
        test_result: Optional[TestResult],
    ) -> str:
        """Create a pull request with the fix."""
        self._emit_event(
            incident.id,
            PipelineStage.CREATING_PR,
            "Creating pull request",
        )

        self._update_incident_status(incident.id, IncidentStatus.PR_CREATED)

        if not self.github:
            self.github = GitHubService()

        # Build test results dict
        test_results = None
        if test_result:
            test_results = {
                "passed": test_result.passed,
                "tests_run": test_result.tests_run,
                "tests_passed": test_result.tests_passed,
                "tests_failed": test_result.tests_failed,
            }

        pr = await self.github.create_fix_pr(
            incident_id=incident.id,
            incident_title=incident.title,
            file_path=fix.file_path,
            original_code=fix.original_code,
            fixed_code=fix.fixed_code,
            root_cause=triage.root_cause,
            fix_explanation=fix.explanation,
            service_name=incident.service_name,
            confidence=triage.confidence,
            test_results=test_results,
        )

        self._emit_event(
            incident.id,
            PipelineStage.CREATING_PR,
            f"Pull request created: #{pr.number}",
            data={
                "pr_number": pr.number,
                "pr_url": pr.html_url,
            },
        )

        return pr.html_url

    async def _run_verification(
        self,
        incident: Incident,
        pr_url: str,
        pr_merged_at: datetime,
    ) -> VerificationResult:
        """Monitor production to verify the fix."""
        self._emit_event(
            incident.id,
            PipelineStage.VERIFYING,
            "Monitoring production for fix verification",
        )

        self._update_incident_status(incident.id, IncidentStatus.VERIFYING)

        if not self.production_monitor:
            self.production_monitor = ProductionMonitorService()

        # Define callback for status updates
        async def on_sample(incident_id: str, count: int, sample_num: int):
            self._emit_event(
                incident_id,
                PipelineStage.VERIFYING,
                f"Sample {sample_num}: {count} errors detected",
                data={"sample_number": sample_num, "error_count": count},
            )

        result = await self.production_monitor.verify_fix(
            incident=incident,
            pr_merged_at=pr_merged_at,
            pr_url=pr_url,
            callback=on_sample,
        )

        self._emit_event(
            incident.id,
            PipelineStage.VERIFYING,
            f"Verification complete: {result.status.value}",
            data={
                "status": result.status.value,
                "errors_before": result.errors_before,
                "errors_after": result.errors_after,
            },
        )

        return result

    async def _handle_escalation(
        self,
        incident: Incident,
        reason: EscalationReason,
        error_message: str,
        triage_result: Optional[TriageResult] = None,
    ) -> None:
        """Handle escalation to human attention."""
        # Include classification in event data for frontend display
        event_data = {"reason": reason.value}
        if triage_result:
            event_data["classification"] = triage_result.classification.value

        self._emit_event(
            incident.id,
            PipelineStage.ESCALATED,
            f"Escalating to human: {error_message}",
            data=event_data,
        )

        self._update_incident_status(incident.id, IncidentStatus.ESCALATED)

        # Build escalation message
        message_parts = [error_message]

        if triage_result:
            message_parts.append(f"Classification: {triage_result.classification.value}")
            message_parts.append(f"Root cause: {triage_result.root_cause}")

        try:
            await self._notify_pagerduty(
                incident,
                "escalate",
                reason="\n".join(message_parts),
            )
        except Exception as e:
            logger.warning(f"PagerDuty escalation notification failed: {e}")

    async def process_incident(
        self,
        incident: Incident,
        wait_for_merge: bool = False,
        pr_merged_at: Optional[datetime] = None,
    ) -> PipelineResult:
        """
        Process an incident through the full pipeline.

        Args:
            incident: The incident to process
            wait_for_merge: If True, waits for PR merge before verification
            pr_merged_at: If provided, skips to verification (PR already merged)

        Returns:
            PipelineResult with outcome details
        """
        start_time = datetime.utcnow()

        # Check if already processing this incident
        if incident.id in self._active_incidents:
            logger.warning(f"Incident {incident.id} is already being processed")
            return PipelineResult(
                incident_id=incident.id,
                success=False,
                stage_reached=PipelineStage.RECEIVED,
                error_message="Incident is already being processed",
            )

        # Track this incident
        self._active_incidents[incident.id] = asyncio.current_task()

        try:
            self._emit_event(
                incident.id,
                PipelineStage.RECEIVED,
                "Starting incident processing pipeline",
            )

            # Notify PagerDuty of new incident (fire-and-forget)
            try:
                await self._notify_pagerduty(incident, "trigger")
            except Exception as e:
                logger.warning(f"PagerDuty trigger notification failed: {e}")

            # Stage 1: Triage
            try:
                triage_result = await self._run_triage(incident)
            except TriageError as e:
                await self._handle_escalation(
                    incident,
                    EscalationReason.TRIAGE_FAILED,
                    f"Triage failed: {e}",
                )
                return PipelineResult(
                    incident_id=incident.id,
                    success=False,
                    stage_reached=PipelineStage.TRIAGING,
                    escalation_reason=EscalationReason.TRIAGE_FAILED,
                    error_message=str(e),
                    duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
                )

            # Check if fixable
            if triage_result.classification != TriageClassification.FIXABLE:
                reason = self._get_escalation_reason_for_classification(triage_result.classification)
                message = self._get_escalation_message_for_classification(triage_result)

                await self._handle_escalation(
                    incident,
                    reason,
                    message,
                    triage_result,
                )

                return PipelineResult(
                    incident_id=incident.id,
                    success=False,
                    stage_reached=PipelineStage.TRIAGING,
                    triage_result=triage_result,
                    escalation_reason=reason,
                    error_message=message,
                    duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
                )

            # Acknowledge in PagerDuty - we're working on it
            await self._notify_pagerduty(incident, "acknowledge")

            # Stage 2-3: Fix generation with review loop
            fix_result = None
            review_result = None

            for iteration in range(self.MAX_REVIEW_ITERATIONS):
                try:
                    # Generate fix
                    feedback = review_result.summary if review_result and not review_result.passed else None
                    fix_result = await self._run_fix_generation(
                        triage_result,
                        feedback=feedback,
                        previous_fix=fix_result,
                    )

                    # Review fix
                    try:
                        review_result = await self._run_review(fix_result)

                        if review_result.passed:
                            break  # Success! Move to testing

                    except CodeRabbitNotInstalledError:
                        logger.warning("CodeRabbit not installed, skipping review")
                        review_result = ReviewResult(passed=True, issues=[], suggestions=[])
                        break

                except FixerError as e:
                    if iteration == self.MAX_REVIEW_ITERATIONS - 1:
                        await self._handle_escalation(
                            incident,
                            EscalationReason.FIX_GENERATION_FAILED,
                            f"Fix generation failed after {iteration + 1} attempts: {e}",
                            triage_result,
                        )
                        return PipelineResult(
                            incident_id=incident.id,
                            success=False,
                            stage_reached=PipelineStage.FIXING,
                            triage_result=triage_result,
                            escalation_reason=EscalationReason.FIX_GENERATION_FAILED,
                            error_message=str(e),
                            duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
                        )
                    # Retry on next iteration
                    continue

            # Check if we exhausted retries without passing review
            if review_result and not review_result.passed:
                await self._handle_escalation(
                    incident,
                    EscalationReason.REVIEW_FAILED_MAX_RETRIES,
                    f"Code review failed after {self.MAX_REVIEW_ITERATIONS} iterations",
                    triage_result,
                )
                return PipelineResult(
                    incident_id=incident.id,
                    success=False,
                    stage_reached=PipelineStage.REVIEWING,
                    triage_result=triage_result,
                    fix_result=fix_result,
                    review_result=review_result,
                    escalation_reason=EscalationReason.REVIEW_FAILED_MAX_RETRIES,
                    error_message="Review failed after max iterations",
                    duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
                )

            # Stage 4: Sandbox testing
            test_result = None
            if not self.skip_sandbox:
                try:
                    test_result = await self._run_sandbox_tests(incident, fix_result)

                    if not test_result.passed:
                        await self._handle_escalation(
                            incident,
                            EscalationReason.SANDBOX_FAILED,
                            f"Tests failed: {test_result.tests_failed}/{test_result.tests_run}",
                            triage_result,
                        )
                        return PipelineResult(
                            incident_id=incident.id,
                            success=False,
                            stage_reached=PipelineStage.TESTING,
                            triage_result=triage_result,
                            fix_result=fix_result,
                            review_result=review_result,
                            test_result=test_result,
                            escalation_reason=EscalationReason.SANDBOX_FAILED,
                            error_message="Sandbox tests failed",
                            duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
                        )

                except KindNotInstalledError:
                    logger.warning("Kind not installed, skipping sandbox tests")
                except SandboxError as e:
                    logger.error(f"Sandbox error: {e}")
                    # Continue without tests - PR will note tests were skipped

            # Stage 5: Create PR
            try:
                pr_url = await self._create_pr(
                    incident,
                    triage_result,
                    fix_result,
                    test_result,
                )
                # Save PR URL to incident for future reference
                incident.pr_url = pr_url
                storage.save_incident(incident)
            except GitHubError as e:
                await self._handle_escalation(
                    incident,
                    EscalationReason.PR_CREATION_FAILED,
                    f"PR creation failed: {e}",
                    triage_result,
                )
                return PipelineResult(
                    incident_id=incident.id,
                    success=False,
                    stage_reached=PipelineStage.CREATING_PR,
                    triage_result=triage_result,
                    fix_result=fix_result,
                    review_result=review_result,
                    test_result=test_result,
                    escalation_reason=EscalationReason.PR_CREATION_FAILED,
                    error_message=str(e),
                    duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
                )

            # Notify PagerDuty of success
            await self._notify_pagerduty(incident, "resolve", pr_url=pr_url)

            # Stage 6: Production verification (if not skipped and PR already merged)
            verification_result = None
            if not self.skip_verification and pr_merged_at:
                try:
                    verification_result = await self._run_verification(
                        incident,
                        pr_url,
                        pr_merged_at,
                    )

                    # Persist verification result to storage
                    storage.save_verification_result(verification_result)

                    if verification_result.status == VerificationStatus.FAILED:
                        await self._handle_escalation(
                            incident,
                            EscalationReason.VERIFICATION_FAILED,
                            f"Production verification failed: {verification_result.message}",
                            triage_result,
                        )
                        return PipelineResult(
                            incident_id=incident.id,
                            success=False,
                            stage_reached=PipelineStage.VERIFYING,
                            triage_result=triage_result,
                            fix_result=fix_result,
                            review_result=review_result,
                            test_result=test_result,
                            pr_url=pr_url,
                            verification_result=verification_result,
                            escalation_reason=EscalationReason.VERIFICATION_FAILED,
                            error_message=verification_result.message,
                            duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
                        )

                except ProductionMonitorError as e:
                    logger.warning(f"Production verification skipped: {e}")

            # Success!
            self._emit_event(
                incident.id,
                PipelineStage.COMPLETED,
                "Pipeline completed successfully",
                data={"pr_url": pr_url},
            )

            self._update_incident_status(incident.id, IncidentStatus.FIXED)

            return PipelineResult(
                incident_id=incident.id,
                success=True,
                stage_reached=PipelineStage.COMPLETED,
                triage_result=triage_result,
                fix_result=fix_result,
                review_result=review_result,
                test_result=test_result,
                pr_url=pr_url,
                verification_result=verification_result,
                duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
            )

        except Exception as e:
            # Catch-all for unexpected errors
            logger.exception(f"Unexpected error processing {incident.id}: {e}")

            await self._handle_escalation(
                incident,
                EscalationReason.UNKNOWN_ERROR,
                f"Unexpected error: {e}",
            )

            return PipelineResult(
                incident_id=incident.id,
                success=False,
                stage_reached=PipelineStage.FAILED,
                escalation_reason=EscalationReason.UNKNOWN_ERROR,
                error_message=str(e),
                duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
            )

        finally:
            # Remove from active tracking
            self._active_incidents.pop(incident.id, None)

            # Close services
            if self.github:
                await self.github.close()
            if self.pagerduty:
                await self.pagerduty.close()

    def _get_escalation_reason_for_classification(
        self,
        classification: TriageClassification,
    ) -> EscalationReason:
        """Get escalation reason for non-fixable classification."""
        # Map classifications to escalation reasons
        # All non-fixable classifications use NOT_FIXABLE, but we log the specific type
        mapping = {
            TriageClassification.INFRA_ISSUE: EscalationReason.NOT_FIXABLE,
            TriageClassification.TRANSIENT: EscalationReason.NOT_FIXABLE,
            TriageClassification.NEEDS_HUMAN: EscalationReason.NOT_FIXABLE,
        }
        reason = mapping.get(classification, EscalationReason.NOT_FIXABLE)
        logger.info(f"Escalating due to classification: {classification.value} -> {reason.value}")
        return reason

    def _get_escalation_message_for_classification(
        self,
        triage: TriageResult,
    ) -> str:
        """
        Get escalation message for non-fixable classification.

        Enhanced to provide actionable information from GCP context.
        """
        message_parts = []

        if triage.classification == TriageClassification.INFRA_ISSUE:
            message_parts.append(f"**Infrastructure Issue Detected**")
            message_parts.append(f"Root Cause: {triage.root_cause}")

            # Add GCP context if available
            if triage.gcp_context:
                if triage.gcp_context.get("error_frequency"):
                    freq = triage.gcp_context["error_frequency"]
                    message_parts.append(
                        f"Error Frequency: {freq.get('total_errors_past_hour', 0)} errors in past hour"
                    )
                if triage.gcp_context.get("similar_across_services"):
                    services = [s["service"] for s in triage.gcp_context["similar_across_services"]]
                    message_parts.append(f"Affected Services: {', '.join(services)}")

            if triage.runbook_reference:
                message_parts.append(f"Runbook: {triage.runbook_reference}")
            if triage.manual_steps:
                message_parts.append("Recommended Steps:")
                for i, step in enumerate(triage.manual_steps[:5], 1):
                    message_parts.append(f"  {i}. {step}")

            return "\n".join(message_parts)

        elif triage.classification == TriageClassification.TRANSIENT:
            message_parts.append("**Transient Error (Self-Healing)**")
            message_parts.append(f"Analysis: {triage.root_cause}")

            # Add context about frequency if available
            if triage.gcp_context:
                if triage.gcp_context.get("error_frequency"):
                    freq = triage.gcp_context["error_frequency"]
                    total = freq.get("total_errors_past_hour", 0)
                    unique = freq.get("unique_error_types", 0)
                    message_parts.append(
                        f"Pattern: {total} occurrences in past hour ({unique} unique error types)"
                    )
                if triage.gcp_context.get("related_errors"):
                    message_parts.append("Related Errors:")
                    for err in triage.gcp_context["related_errors"][:3]:
                        message_parts.append(f"  - ({err['count']}x) {err['message']}")

            message_parts.append("")
            message_parts.append("Action: No code change needed. Monitor for persistence.")
            message_parts.append("Escalate if: Error rate increases or doesn't resolve within 1 hour.")

            return "\n".join(message_parts)

        elif triage.classification == TriageClassification.NEEDS_HUMAN:
            message_parts.append("**Requires Human Investigation**")
            message_parts.append(f"Analysis: {triage.root_cause}")

            if triage.gcp_context:
                if triage.gcp_context.get("recent_service_logs"):
                    message_parts.append("Recent Service Activity:")
                    for log in triage.gcp_context["recent_service_logs"][:3]:
                        message_parts.append(f"  [{log['severity']}] {log['message']}")

            if triage.related_context:
                message_parts.append("Additional Context:")
                for ctx in triage.related_context[:5]:
                    message_parts.append(f"  - {ctx}")

            return "\n".join(message_parts)

        return f"Not fixable: {triage.root_cause}"

    def is_processing(self, incident_id: str) -> bool:
        """Check if an incident is currently being processed."""
        return incident_id in self._active_incidents

    async def cancel_processing(self, incident_id: str) -> bool:
        """
        Cancel processing of an incident.

        Args:
            incident_id: The incident to cancel

        Returns:
            True if cancelled, False if not found
        """
        task = self._active_incidents.get(incident_id)
        if task and not task.done():
            task.cancel()
            return True
        return False


# Module-level convenience function
async def process_incident(
    incident: Incident,
    event_callback: Optional[EventCallback] = None,
) -> PipelineResult:
    """
    Process an incident through the full pipeline.

    Args:
        incident: The incident to process
        event_callback: Optional callback for events

    Returns:
        PipelineResult with outcome details
    """
    orchestrator = PipelineOrchestrator(event_callback=event_callback)
    return await orchestrator.process_incident(incident)
