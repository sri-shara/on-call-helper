# On Call Helper - Architecture & Implementation Guide

A standalone AI-powered incident response agent that monitors the Nucleus MDR platform for production errors, automatically triages issues, generates fixes, validates them thoroughly, and creates PRs.

---

## Repository References

> **IMPORTANT**: When implementing this system, always reference these repositories for context:

### 1. Nucleus Repository
**Path**: `/Users/sri/nucleus`

**What it is**: The main MDR (Managed Detection & Response) platform being monitored.

**Key areas to reference**:
- `backend/services/` - All Go microservices (42+ services)
- `backend/processors/` - Async Pub/Sub workers (16+ processors)
- `backend/db/nucleus/schema/` - Database schemas
- `test/smoke/` - Smoke test suite
- `Taskfile.yaml` - Build/test commands (`task test`, `task lint`)
- `Tiltfile` - Local K8s development setup
- `k8s/` - Kubernetes configurations
- `localdev/` - Local development utilities

**Tech stack**: Go 1.24+, PostgreSQL 15+, Google Cloud Pub/Sub, Kubernetes, Temporal

### 2. On-Call Repository
**Path**: `/Users/sri/oncall`

**What it is**: The on-call engineering handbook with runbooks, SRE knowledge, and triage procedures.

**Key areas to reference**:
- `.claude/commands/sre-triage.md` - Main triage framework
- `.claude/commands/sre-triage/` - Triage sub-components:
  - `infrastructure-checks.md` - AlloyDB, Pub/Sub diagnostics
  - `bigquery-queries.md` - Data validation queries
  - `tenant-reference.md` - Production vs demo tenant list
  - `error-patterns.md` - Known patterns and resolutions
  - `output-format.md` - Structured triage output
- `runbooks/` - Alert-specific playbooks:
  - `alloydb.md` - Database issues
  - `pubsub-backlogs.md` - Message queue issues
  - `cloud-run.md` - Service errors
  - `integrations.md` - Third-party integration errors
- `scripts/oncall-checkout.sh` - Health check automation
- `handoffs/` - Shift documentation structure
- `postmortems/` - Incident review templates

---

## System Overview

### What On Call Helper Does

1. **Detects** production errors from GCP Cloud Logging (Nucleus infrastructure)
2. **Filters** out transient/self-healing errors and demo tenant noise
3. **Triages** using Claude AI embedded with SRE knowledge from the oncall repo
4. **Generates** minimal code fixes using Claude AI
5. **Validates** fixes through multi-stage pipeline:
   - Static analysis (Go build, vet, lint)
   - CodeRabbit code review (with retry loop)
   - Sandbox testing in ephemeral Kind clusters
   - Optional staging deployment
6. **Creates** GitHub PRs for approved fixes
7. **Verifies** fixes resolved the issue in production post-deployment
8. **Notifies** team via PagerDuty throughout the process

### Key Value Proposition

- **Automated triage**: Uses embedded SRE runbook knowledge
- **Safe testing**: Fixes tested in isolated Kind clusters matching Nucleus's architecture
- **Production verification**: Confirms fixes actually work after deployment
- **Tenant awareness**: Ignores demo tenant errors, focuses on production
- **Transient filtering**: Skips known self-healing error patterns
- **Full audit trail**: Every step logged and visible in dashboard

---

## Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         GCP CLOUD LOGGING (Nucleus Infra)                        │
│  Errors from: Cloud Run services, AlloyDB, Pub/Sub processors, API-proxy        │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     ON CALL HELPER (Standalone FastAPI App)                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                         ERROR INGESTION LAYER                             │   │
│  │  • GCP Log Sink → Pub/Sub → Push webhook                                  │   │
│  │  • Filter: severity >= ERROR, exclude demo tenants                        │   │
│  │  • Deduplication: track insertIds, group related errors                   │   │
│  │  • Transient Filter: skip known self-healing patterns                     │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                                     ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                    TRIAGE AGENT (Claude + SRE Knowledge)                  │   │
│  │  • Embedded: /sre-triage runbooks, error-patterns, tenant-reference       │   │
│  │  • Infrastructure checks: AlloyDB health, Pub/Sub backlogs                │   │
│  │  • BigQuery validation queries                                            │   │
│  │  • Output: root cause, affected service, file path, confidence            │   │
│  │  • Decision: FIXABLE vs INFRA_ISSUE vs TRANSIENT vs NEEDS_HUMAN           │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                          ┌──────────┴──────────┐                                │
│                          │ Is it FIXABLE?       │                                │
│                          └──────────┬──────────┘                                │
│                    NO ──────────────┼────────────── YES                         │
│                    │                │                │                          │
│                    ▼                │                ▼                          │
│           Log & Alert               │    ┌───────────────────────────────────┐  │
│           (PagerDuty)               │    │         FIXER AGENT (Claude)      │  │
│                                     │    │  • Read actual source from GitHub │  │
│                                     │    │  • Generate minimal fix           │  │
│                                     │    │  • Include fix explanation        │  │
│                                     │    └───────────────────────┬───────────┘  │
│                                     │                            │              │
│                                     │                            ▼              │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                      VALIDATION PIPELINE (Multi-Stage)                    │   │
│  │                                                                           │   │
│  │  Stage 1: STATIC ANALYSIS                                                 │   │
│  │  ├── Go build check (syntax, imports)                                     │   │
│  │  ├── Go vet (common mistakes)                                             │   │
│  │  └── golangci-lint (comprehensive linting)                                │   │
│  │                                                                           │   │
│  │  Stage 2: CODE REVIEW                                                     │   │
│  │  ├── CodeRabbit CLI review                                                │   │
│  │  └── If issues → feedback to Fixer Agent (max 3 iterations)               │   │
│  │                                                                           │   │
│  │  Stage 3: SANDBOX TESTING (Ephemeral Kind Cluster)                        │   │
│  │  ├── Spin up isolated Kind cluster                                        │   │
│  │  ├── Deploy via Tilt/Helm with fix applied                                │   │
│  │  ├── Run: task test (Go unit tests)                                       │   │
│  │  ├── Run: smoke tests (test/smoke/)                                       │   │
│  │  └── Tear down cluster                                                    │   │
│  │                                                                           │   │
│  │  Stage 4: STAGING DEPLOYMENT (Optional but Recommended)                   │   │
│  │  ├── Deploy to staging environment                                        │   │
│  │  ├── Replay error-triggering scenario                                     │   │
│  │  └── Verify error doesn't recur                                           │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                          All stages passed?                                      │
│                                     │                                            │
│                                     ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                         PR CREATION (GitHub)                              │   │
│  │  • Branch: oncall-helper/fix-{incident_id}                                │   │
│  │  • PR body: root cause, fix explanation, test results, verification plan │   │
│  │  • Labels: oncall-helper, auto-fix, tests-passed                          │   │
│  │  • Status: Draft PR (requires human approval to merge)                    │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                                     ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                    PRODUCTION VERIFICATION (Post-Merge)                   │   │
│  │  • Monitor: Same error signature in Cloud Logging                         │   │
│  │  • Window: 30 min - 2 hours after deployment                              │   │
│  │  • Success: Error rate drops to 0 or acceptable threshold                 │   │
│  │  • Failure: Alert on-call, consider rollback                              │   │
│  │  • Update: Handoff doc with resolution status                             │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                         WEBSOCKET MANAGER                                 │   │
│  │  Broadcasts real-time events to dashboard clients                         │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              REACT DASHBOARD                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│  • Incident Feed: List of incidents with status                                 │
│  • Agent Thinking: Real-time agent activity visualization                       │
│  • Code Diff: Before/after code comparison                                      │
│  • Sandbox Status: Test execution progress                                      │
│  • Production Verification: Post-deploy monitoring                              │
│  • Metrics Panel: MTTR, success rate, counts                                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
on-call-helper/
├── backend/
│   ├── main.py                     # FastAPI app, routes, WebSocket endpoint
│   ├── config.py                   # Environment configuration
│   ├── websocket_manager.py        # Connection management, event broadcasting
│   ├── storage.py                  # In-memory storage, metrics tracking
│   ├── models/
│   │   └── incident.py             # Pydantic models for all entities
│   ├── agents/
│   │   ├── orchestrator.py         # Pipeline coordinator
│   │   ├── triage.py               # Claude + embedded SRE knowledge
│   │   └── fixer.py                # Claude fix generation
│   ├── services/
│   │   ├── gcp_logging.py          # GCP log ingestion (webhook + polling)
│   │   ├── coderabbit.py           # Code review via CLI
│   │   ├── sandbox.py              # Kind cluster management
│   │   ├── github.py               # PR creation
│   │   ├── pagerduty.py            # Team notifications
│   │   └── production_monitor.py   # Post-deploy verification
│   ├── filters/
│   │   ├── transient.py            # Self-healing error filter
│   │   └── tenant.py               # Demo tenant filter
│   └── knowledge/                  # Embedded SRE knowledge (from oncall repo)
│       ├── __init__.py
│       ├── loader.py               # Load knowledge from oncall repo
│       ├── runbooks/               # Symlink or copy from /Users/sri/oncall/runbooks
│       └── sre_triage/             # Symlink or copy from /Users/sri/oncall/.claude/commands/sre-triage
├── frontend/
│   └── src/
│       ├── hooks/useWebSocket.js   # WebSocket with auto-reconnect
│       ├── context/IncidentContext.jsx  # Global state management
│       └── components/             # Dashboard UI components
├── sandbox/
│   ├── kind-config.yaml            # Kind cluster configuration
│   ├── deploy.sh                   # Deploy Nucleus to Kind
│   ├── run-tests.sh                # Execute test suite
│   └── cleanup.sh                  # Teardown cluster
├── tests/
│   ├── test_triage.py
│   ├── test_fixer.py
│   └── test_sandbox.py
├── requirements.txt
├── package.json
├── Dockerfile
├── docker-compose.yaml
└── ARCHITECTURE.md                 # This file
```

---

## Component Specifications

### 1. Error Ingestion Layer

**Purpose**: Receive and filter production errors from Nucleus infrastructure

**GCP Setup**:
```bash
# 1. Create Pub/Sub topic for error logs
gcloud pubsub topics create oncall-helper-errors

# 2. Create push subscription pointing to On Call Helper
gcloud pubsub subscriptions create oncall-helper-errors-sub \
  --topic=oncall-helper-errors \
  --push-endpoint=https://your-oncall-helper-domain/webhook/gcp-logs \
  --ack-deadline=60

# 3. Create log sink to route errors to the topic
gcloud logging sinks create oncall-helper-sink \
  pubsub.googleapis.com/projects/PROJECT_ID/topics/oncall-helper-errors \
  --log-filter='severity>=ERROR AND resource.type="cloud_run_revision"'
```

**Webhook Endpoint**:
```python
@app.post("/webhook/gcp-logs")
async def receive_gcp_log(request: Request):
    log_entry = await parse_pubsub_message(request)

    # Apply filters
    if not should_process_error(log_entry):
        return {"status": "filtered"}

    # Create incident and start pipeline
    incident = create_incident_from_log(log_entry)
    await orchestrator.process_incident(incident)

    return {"status": "processing", "incident_id": incident.id}
```

---

### 2. Transient Error Filter

**Purpose**: Skip known self-healing errors that don't need fixes

**Reference**: `/Users/sri/oncall/.claude/commands/sre-triage/error-patterns.md`

```python
# filters/transient.py

TRANSIENT_PATTERNS = [
    {
        "pattern": r"Routing deadline expired",
        "reason": "Transient Cloud Run → AlloyDB connection, auto-retries",
        "action": "IGNORE"
    },
    {
        "pattern": r"context deadline exceeded",
        "reason": "External API timeout, has automatic retry",
        "action": "IGNORE"
    },
    {
        "pattern": r"case number already exists",
        "reason": "Race condition in casemaker, retries successfully",
        "action": "IGNORE"
    },
    {
        "pattern": r"RESOURCE_EXHAUSTED.*Quota exceeded",
        "reason": "Rate limiting, backs off automatically",
        "action": "MONITOR"  # Alert if persists > 10 min
    },
    {
        "pattern": r"connection reset by peer",
        "reason": "Network blip, auto-reconnects",
        "action": "IGNORE"
    },
    {
        "pattern": r"deadline exceeded.*retry",
        "reason": "Transient timeout with retry mechanism",
        "action": "IGNORE"
    },
]

def is_transient_error(error_message: str) -> tuple[bool, str]:
    """Check if error matches known transient patterns."""
    for pattern_info in TRANSIENT_PATTERNS:
        if re.search(pattern_info["pattern"], error_message, re.IGNORECASE):
            return True, pattern_info["reason"]
    return False, ""
```

---

### 3. Tenant Filter

**Purpose**: Ignore errors from demo/test tenants, focus on production

**Reference**: `/Users/sri/oncall/.claude/commands/sre-triage/tenant-reference.md`

```python
# filters/tenant.py

# Demo/Test tenants - errors are usually noise
DEMO_TENANTS = [
    "TENEX POC Demo",
    "Tenex Demo",
    "TENEX Internal",
    "Tenex Sandbox",
    "Test Customer",
    "Demo Organization",
]

# Production tenants - investigate errors
PRODUCTION_TENANTS = [
    "Whitney",
    "Horizontal",
    "Bowtie",
    "Sycamore",
    "Haven",
    "RedRock",
    "Warehouse",
    "IPA",
    "Royals",
    "Kingpins",
    "QuantumLeap",
]

def should_process_tenant(tenant_name: str) -> tuple[bool, str]:
    """Determine if errors from this tenant should be processed."""
    if not tenant_name:
        return True, "No tenant context - processing"

    # Normalize tenant name for comparison
    normalized = tenant_name.strip().lower()

    for demo in DEMO_TENANTS:
        if demo.lower() in normalized or normalized in demo.lower():
            return False, f"Demo tenant ({tenant_name}) - ignoring"

    return True, "Production tenant - processing"
```

---

### 4. Triage Agent (Claude + SRE Knowledge)

**Purpose**: Analyze errors using Claude with embedded SRE runbook knowledge

**Knowledge Loading**:
```python
# knowledge/loader.py

import os
from pathlib import Path

ONCALL_REPO_PATH = Path("/Users/sri/oncall")

def load_sre_knowledge() -> dict:
    """Load all SRE knowledge from the oncall repository."""

    knowledge = {
        # Main triage framework
        "triage_framework": _load_file(
            ONCALL_REPO_PATH / ".claude/commands/sre-triage.md"
        ),

        # Triage sub-components
        "infrastructure_checks": _load_file(
            ONCALL_REPO_PATH / ".claude/commands/sre-triage/infrastructure-checks.md"
        ),
        "bigquery_queries": _load_file(
            ONCALL_REPO_PATH / ".claude/commands/sre-triage/bigquery-queries.md"
        ),
        "tenant_reference": _load_file(
            ONCALL_REPO_PATH / ".claude/commands/sre-triage/tenant-reference.md"
        ),
        "error_patterns": _load_file(
            ONCALL_REPO_PATH / ".claude/commands/sre-triage/error-patterns.md"
        ),
        "output_format": _load_file(
            ONCALL_REPO_PATH / ".claude/commands/sre-triage/output-format.md"
        ),

        # Runbooks
        "runbooks": {
            "alloydb": _load_file(ONCALL_REPO_PATH / "runbooks/alloydb.md"),
            "pubsub": _load_file(ONCALL_REPO_PATH / "runbooks/pubsub-backlogs.md"),
            "cloud_run": _load_file(ONCALL_REPO_PATH / "runbooks/cloud-run.md"),
            "integrations": _load_file(ONCALL_REPO_PATH / "runbooks/integrations.md"),
            "secops": _load_file(ONCALL_REPO_PATH / "runbooks/secops-integration.md"),
        },
    }

    return knowledge

def _load_file(path: Path) -> str:
    """Load file content, return empty string if not found."""
    try:
        return path.read_text()
    except FileNotFoundError:
        return f"[File not found: {path}]"
```

**Triage Agent System Prompt**:
```python
# agents/triage.py

from knowledge.loader import load_sre_knowledge

SRE_KNOWLEDGE = load_sre_knowledge()

TRIAGE_SYSTEM_PROMPT = f"""
You are an SRE triage agent for Nucleus, a Security Operations Center (SOC) platform
for Managed Detection & Response (MDR) services.

## Your Knowledge Base

### Triage Framework
{SRE_KNOWLEDGE["triage_framework"]}

### Infrastructure Checks
{SRE_KNOWLEDGE["infrastructure_checks"]}

### Known Error Patterns
{SRE_KNOWLEDGE["error_patterns"]}

### Tenant Reference
{SRE_KNOWLEDGE["tenant_reference"]}

### Runbooks

#### AlloyDB Issues
{SRE_KNOWLEDGE["runbooks"]["alloydb"]}

#### Pub/Sub Issues
{SRE_KNOWLEDGE["runbooks"]["pubsub"]}

#### Cloud Run Issues
{SRE_KNOWLEDGE["runbooks"]["cloud_run"]}

#### Integration Issues
{SRE_KNOWLEDGE["runbooks"]["integrations"]}

---

## Your Task

Analyze the production error and determine:

1. **Classification**:
   - FIXABLE: Code bug that can be fixed programmatically
   - INFRA_ISSUE: Infrastructure problem (AlloyDB, Pub/Sub, networking)
   - TRANSIENT: Self-healing error that will resolve
   - NEEDS_HUMAN: Complex issue requiring human judgment

2. **If FIXABLE, provide**:
   - `root_cause`: Detailed explanation of what went wrong
   - `service_name`: Which Nucleus service is affected
   - `file_path`: Specific file in the nucleus repo containing the bug
   - `function_name`: The function with the bug
   - `code_snippet`: The problematic code
   - `suggested_fix`: High-level description of the fix needed
   - `confidence`: 0.0-1.0 score

3. **If INFRA_ISSUE, provide**:
   - `root_cause`: What infrastructure component is failing
   - `runbook_reference`: Which runbook to follow
   - `manual_steps`: Recommended manual intervention

Output your analysis as JSON.
"""
```

**Triage Agent Implementation**:
```python
# agents/triage.py

from anthropic import Anthropic

class TriageAgent:
    def __init__(self):
        self.client = Anthropic()
        self.model = "claude-sonnet-4-20250514"  # Fast, cost-effective

    async def analyze(self, incident: Incident) -> TriageResult:
        """Analyze incident and determine classification."""

        user_prompt = f"""
        Analyze this production error from Nucleus:

        ## Error Details
        - **Service**: {incident.service_name}
        - **Error Message**: {incident.error_message}
        - **Stack Trace**:
        ```
        {incident.stack_trace}
        ```
        - **File Path** (if known): {incident.file_path}
        - **Tenant**: {incident.tenant_name}
        - **Environment**: {incident.environment}
        - **Timestamp**: {incident.created_at}

        Provide your triage analysis as JSON.
        """

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=TRIAGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )

        return self._parse_response(response, incident.id)
```

---

### 5. Fixer Agent (Claude)

**Purpose**: Generate minimal code fixes based on triage analysis

**Reference for Nucleus code patterns**: `/Users/sri/nucleus/backend/`

```python
# agents/fixer.py

FIXER_SYSTEM_PROMPT = """
You are a code fix agent for Nucleus, a Go-based MDR platform.

## Nucleus Codebase Context
- Language: Go 1.24+
- Architecture: Microservices in backend/services/ and backend/processors/
- Database: PostgreSQL with sqlc for type-safe queries
- Testing: Go standard testing + testify
- Style: Follow existing patterns in the codebase

## Your Task
Generate a MINIMAL fix for the identified bug.

Rules:
1. Make the smallest possible change to fix the issue
2. Do NOT refactor unrelated code
3. Do NOT add features or improvements beyond the fix
4. Match existing code style exactly
5. Include error handling if the bug was caused by missing error handling
6. The fix must compile and pass `go build` and `go vet`

## Output Format
Provide your fix as JSON:
{
    "file_path": "backend/services/...",
    "original_code": "the buggy code section",
    "fixed_code": "the corrected code section",
    "explanation": "why this fix works",
    "diff_summary": "brief description of changes"
}
"""

class FixerAgent:
    def __init__(self, github_service: GitHubService):
        self.client = Anthropic()
        self.model = "claude-sonnet-4-20250514"
        self.github = github_service

    async def generate_fix(
        self,
        triage: TriageResult,
        coderabbit_feedback: str | None = None
    ) -> FixResult:
        """Generate code fix based on triage analysis."""

        # Fetch actual source code from GitHub
        source_code = await self.github.get_file_content(
            repo="your-org/nucleus",
            path=triage.file_path,
            ref="main"
        )

        user_prompt = f"""
        ## Bug Analysis
        - **Root Cause**: {triage.root_cause}
        - **File**: {triage.file_path}
        - **Function**: {triage.function_name}
        - **Confidence**: {triage.confidence}

        ## Current Source Code
        ```go
        {source_code}
        ```

        ## Problematic Code Snippet
        ```go
        {triage.code_snippet}
        ```

        {"## Previous CodeRabbit Feedback (address these issues):" + coderabbit_feedback if coderabbit_feedback else ""}

        Generate a minimal fix for this bug.
        """

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=FIXER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )

        return self._parse_response(response, triage.incident_id)
```

---

### 6. Sandbox Service (Kind Clusters)

**Purpose**: Test fixes in isolated Kubernetes environments matching Nucleus architecture

**Why Kind?**
- Nucleus uses Kind + Tilt for local development
- Matches production K8s environment
- Can run actual Go tests and smoke tests
- Uses existing Helm charts and configurations

**Reference**: `/Users/sri/nucleus/localdev/`, `/Users/sri/nucleus/Tiltfile`

```python
# services/sandbox.py

import subprocess
import uuid
from pathlib import Path

NUCLEUS_REPO_PATH = Path("/Users/sri/nucleus")

class SandboxService:
    """Manage ephemeral Kind clusters for testing fixes."""

    async def create_sandbox(self, incident_id: str) -> Sandbox:
        """Create isolated Kind cluster for testing."""

        cluster_name = f"oncall-test-{incident_id[:8]}-{uuid.uuid4().hex[:4]}"

        # Create Kind cluster using Nucleus config
        result = subprocess.run(
            ["kind", "create", "cluster",
             "--name", cluster_name,
             "--config", str(NUCLEUS_REPO_PATH / "localdev/kind-config.yaml")],
            capture_output=True,
            text=True,
            timeout=300  # 5 min timeout
        )

        if result.returncode != 0:
            raise SandboxCreationError(f"Failed to create cluster: {result.stderr}")

        return Sandbox(
            id=cluster_name,
            incident_id=incident_id,
            status="created"
        )

    async def apply_fix(self, sandbox: Sandbox, fix: FixResult) -> None:
        """Apply the code fix to the sandbox environment."""

        # Clone Nucleus repo to temp directory
        work_dir = Path(f"/tmp/oncall-sandbox-{sandbox.id}")
        subprocess.run(
            ["git", "clone", "--depth=1", str(NUCLEUS_REPO_PATH), str(work_dir)],
            check=True
        )

        # Apply the fix
        fix_path = work_dir / fix.file_path
        original_content = fix_path.read_text()
        fixed_content = original_content.replace(fix.original_code, fix.fixed_code)
        fix_path.write_text(fixed_content)

        # Build Docker images with fix
        subprocess.run(
            ["task", "build:docker"],
            cwd=work_dir,
            check=True
        )

        # Load images into Kind cluster
        subprocess.run(
            ["kind", "load", "docker-image", "nucleus:local",
             "--name", sandbox.id],
            check=True
        )

        sandbox.work_dir = work_dir
        sandbox.status = "fix_applied"

    async def run_tests(self, sandbox: Sandbox) -> TestResult:
        """Run test suite in the sandbox."""

        results = {"unit_tests": None, "smoke_tests": None}

        # Run Go unit tests
        unit_result = subprocess.run(
            ["task", "test"],
            cwd=sandbox.work_dir,
            capture_output=True,
            text=True,
            timeout=600  # 10 min timeout
        )
        results["unit_tests"] = {
            "passed": unit_result.returncode == 0,
            "output": unit_result.stdout + unit_result.stderr
        }

        # Run smoke tests if unit tests passed
        if unit_result.returncode == 0:
            # Deploy to Kind cluster first
            subprocess.run(
                ["tilt", "up", "--port=0"],
                cwd=sandbox.work_dir,
                timeout=300
            )

            smoke_result = subprocess.run(
                ["task", "test:smoke"],
                cwd=sandbox.work_dir,
                capture_output=True,
                text=True,
                timeout=600
            )
            results["smoke_tests"] = {
                "passed": smoke_result.returncode == 0,
                "output": smoke_result.stdout + smoke_result.stderr
            }

        return TestResult(
            incident_id=sandbox.incident_id,
            passed=all(r["passed"] for r in results.values() if r),
            unit_tests=results["unit_tests"],
            smoke_tests=results["smoke_tests"]
        )

    async def cleanup(self, sandbox: Sandbox) -> None:
        """Delete Kind cluster and cleanup resources."""

        subprocess.run(
            ["kind", "delete", "cluster", "--name", sandbox.id],
            capture_output=True
        )

        if sandbox.work_dir and sandbox.work_dir.exists():
            subprocess.run(["rm", "-rf", str(sandbox.work_dir)])
```

**Sandbox Runner Infrastructure**:
```yaml
# Required specs for sandbox runner VM/container
compute:
  cpu: 8 cores (minimum)
  memory: 32GB RAM
  disk: 100GB SSD

# Can run on:
- GCE instance: n2-standard-8
- Self-hosted runner with Docker + Kind
- Dedicated build server
```

---

### 7. CodeRabbit Service

**Purpose**: Automated code review before sandbox testing

```python
# services/coderabbit.py

import subprocess
import tempfile
import json

class CodeRabbitService:
    """Code review via CodeRabbit CLI."""

    MAX_ITERATIONS = 3
    BLOCKING_SEVERITIES = ["critical", "high", "error", "security"]

    async def review(self, fix: FixResult) -> ReviewResult:
        """Run CodeRabbit review on the fix."""

        # Write fixed code to temp file with correct extension
        suffix = ".go"  # Nucleus is Go
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=suffix,
            delete=False
        ) as f:
            f.write(fix.fixed_code)
            temp_path = f.name

        try:
            result = subprocess.run(
                ["coderabbit", "review", temp_path, "--format", "json"],
                capture_output=True,
                text=True,
                timeout=120
            )

            review_data = json.loads(result.stdout)

            # Check for blocking issues
            blocking_issues = [
                issue for issue in review_data.get("issues", [])
                if issue.get("severity") in self.BLOCKING_SEVERITIES
            ]

            return ReviewResult(
                passed=len(blocking_issues) == 0,
                issues=review_data.get("issues", []),
                suggestions=review_data.get("suggestions", []),
                summary=self._format_feedback(blocking_issues)
            )

        finally:
            os.unlink(temp_path)

    def _format_feedback(self, issues: list) -> str:
        """Format issues as feedback for Claude fixer agent."""
        if not issues:
            return ""

        feedback = "CodeRabbit found the following issues:\n\n"
        for issue in issues:
            feedback += f"- [{issue['severity']}] Line {issue.get('line', '?')}: {issue['message']}\n"
            if issue.get('suggestion'):
                feedback += f"  Suggestion: {issue['suggestion']}\n"

        return feedback
```

---

### 8. GitHub Service

**Purpose**: Create PRs for validated fixes

**Reference for PR format**: Standard Nucleus PR conventions

```python
# services/github.py

from github import Github

class GitHubService:
    def __init__(self, token: str, repo: str):
        self.client = Github(token)
        self.repo = self.client.get_repo(repo)

    async def create_fix_pr(
        self,
        incident: Incident,
        triage: TriageResult,
        fix: FixResult,
        test_results: TestResult
    ) -> str:
        """Create a PR for the validated fix."""

        # Create branch
        branch_name = f"oncall-helper/fix-{incident.id}"
        base_branch = self.repo.get_branch("main")
        self.repo.create_git_ref(
            ref=f"refs/heads/{branch_name}",
            sha=base_branch.commit.sha
        )

        # Get current file content
        file = self.repo.get_contents(fix.file_path, ref="main")

        # Update file with fix
        current_content = file.decoded_content.decode()
        new_content = current_content.replace(fix.original_code, fix.fixed_code)

        self.repo.update_file(
            path=fix.file_path,
            message=f"fix: {incident.title}\n\nAuto-generated by On Call Helper",
            content=new_content,
            sha=file.sha,
            branch=branch_name
        )

        # Create PR
        pr_body = self._generate_pr_body(incident, triage, fix, test_results)

        pr = self.repo.create_pull(
            title=f"[On Call Helper] Fix: {incident.title[:50]}",
            body=pr_body,
            head=branch_name,
            base="main",
            draft=True  # Draft PR requires human approval
        )

        # Add labels
        pr.add_to_labels("oncall-helper", "auto-fix", "tests-passed")

        return pr.html_url

    def _generate_pr_body(
        self,
        incident: Incident,
        triage: TriageResult,
        fix: FixResult,
        test_results: TestResult
    ) -> str:
        return f"""## On Call Helper Auto-Generated Fix

**Status:** All tests passed | Draft PR (requires human approval)

### Incident Details
| Field | Value |
|-------|-------|
| ID | `{incident.id}` |
| Title | {incident.title} |
| Service | {incident.service_name} |
| File | `{fix.file_path}` |
| Severity | {incident.severity} |
| Tenant | {incident.tenant_name or "N/A"} |

### Root Cause Analysis
> {triage.root_cause}

**Confidence:** {triage.confidence:.0%}

### Changes Made
{fix.explanation}

### Code Diff
```diff
- {fix.original_code}
+ {fix.fixed_code}
```

### Test Results
- Unit Tests: {"Passed" if test_results.unit_tests["passed"] else "Failed"}
- Smoke Tests: {"Passed" if test_results.smoke_tests and test_results.smoke_tests["passed"] else "Skipped/Failed"}

### Verification Checklist
- [x] Sandbox tests passed
- [ ] Manual code review
- [ ] Verify fix in staging
- [ ] Monitor production after merge

### Production Verification Plan
After merging, On Call Helper will monitor Cloud Logging for 2 hours to verify the error no longer occurs.

---
Generated by On Call Helper | [View Incident Dashboard](your-dashboard-url)
"""
```

---

### 9. Production Verification Service

**Purpose**: Confirm fixes actually resolve issues in production

```python
# services/production_monitor.py

from google.cloud import logging_v2
import asyncio

class ProductionMonitorService:
    """Monitor production after fix deployment to verify resolution."""

    def __init__(self, project_id: str):
        self.client = logging_v2.Client(project=project_id)
        self.check_interval_seconds = 300  # 5 minutes
        self.monitoring_duration_hours = 2

    async def verify_fix(
        self,
        incident: Incident,
        pr_merged_at: datetime
    ) -> VerificationResult:
        """Monitor production to verify fix resolved the issue."""

        # Build query for the same error signature
        error_filter = self._build_error_filter(incident)

        # Monitor for 2 hours
        end_time = pr_merged_at + timedelta(hours=self.monitoring_duration_hours)
        error_counts = []

        while datetime.utcnow() < end_time:
            await asyncio.sleep(self.check_interval_seconds)

            # Count errors since deployment
            count = await self._count_errors(
                error_filter,
                start_time=pr_merged_at,
                end_time=datetime.utcnow()
            )
            error_counts.append(count)

            # Broadcast status update
            await self._broadcast_verification_status(
                incident.id,
                count,
                datetime.utcnow()
            )

        # Analyze results
        total_errors_after = sum(error_counts)

        # Get baseline (errors before fix in same time window)
        baseline_count = await self._count_errors(
            error_filter,
            start_time=pr_merged_at - timedelta(hours=self.monitoring_duration_hours),
            end_time=pr_merged_at
        )

        # Determine success
        if total_errors_after == 0:
            status = "SUCCESS"
            message = "Error completely resolved - no occurrences since deployment"
        elif total_errors_after < baseline_count * 0.1:
            status = "SUCCESS"
            message = f"Error reduced by >90% ({baseline_count} → {total_errors_after})"
        elif total_errors_after < baseline_count:
            status = "PARTIAL"
            message = f"Error reduced but not eliminated ({baseline_count} → {total_errors_after})"
        else:
            status = "FAILED"
            message = f"Error persists or increased ({baseline_count} → {total_errors_after})"

        return VerificationResult(
            incident_id=incident.id,
            status=status,
            message=message,
            errors_before=baseline_count,
            errors_after=total_errors_after,
            monitoring_duration_hours=self.monitoring_duration_hours
        )

    def _build_error_filter(self, incident: Incident) -> str:
        """Build Cloud Logging filter for this specific error."""
        # Match on error message pattern and service
        escaped_msg = incident.error_message[:100].replace('"', '\\"')
        return f'''
            severity>=ERROR
            AND resource.labels.service_name="{incident.service_name}"
            AND textPayload:"{escaped_msg}"
        '''
```

---

### 10. PagerDuty Service

**Purpose**: Notify team about incidents and resolution status

**Reference**: Nucleus already uses PagerDuty for on-call (see `/Users/sri/oncall/README.md`)

```python
# services/pagerduty.py

import httpx

class PagerDutyService:
    """PagerDuty Events API v2 integration."""

    EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

    def __init__(self, routing_key: str):
        self.routing_key = routing_key

    async def trigger(self, incident: Incident) -> str:
        """Trigger new PagerDuty incident."""
        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": f"oncall-helper-{incident.id}",
            "payload": {
                "summary": f"[On Call Helper] {incident.title}",
                "severity": self._map_severity(incident.severity),
                "source": "on-call-helper",
                "custom_details": {
                    "incident_id": incident.id,
                    "service": incident.service_name,
                    "error_message": incident.error_message[:500],
                    "tenant": incident.tenant_name,
                    "dashboard_url": f"https://your-dashboard/{incident.id}"
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.EVENTS_URL, json=payload)
            return response.json().get("dedup_key")

    async def acknowledge(self, incident_id: str) -> None:
        """Acknowledge - pipeline is processing."""
        await self._send_event(incident_id, "acknowledge")

    async def resolve(self, incident_id: str, pr_url: str) -> None:
        """Resolve - fix successful."""
        await self._send_event(
            incident_id,
            "resolve",
            custom_details={"pr_url": pr_url, "status": "Fix PR created"}
        )

    async def escalate(self, incident_id: str, reason: str) -> None:
        """Escalate - automated fix failed."""
        # Trigger new high-severity incident for human attention
        await self._send_event(
            f"{incident_id}-escalation",
            "trigger",
            severity="high",
            summary=f"[On Call Helper] ESCALATION: {reason}"
        )

    def _map_severity(self, severity: str) -> str:
        mapping = {
            "critical": "critical",
            "high": "error",
            "medium": "warning",
            "low": "info"
        }
        return mapping.get(severity, "warning")
```

---

## Data Models

```python
# models/incident.py

from pydantic import BaseModel
from datetime import datetime
from enum import Enum

class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class IncidentStatus(str, Enum):
    ACTIVE = "active"
    TRIAGING = "triaging"
    FIXING = "fixing"
    TESTING = "testing"
    PR_CREATED = "pr_created"
    VERIFYING = "verifying"
    FIXED = "fixed"
    ESCALATED = "escalated"
    FILTERED = "filtered"

class TriageClassification(str, Enum):
    FIXABLE = "fixable"
    INFRA_ISSUE = "infra_issue"
    TRANSIENT = "transient"
    NEEDS_HUMAN = "needs_human"

class Incident(BaseModel):
    id: str  # Format: "OCH-{8chars}"
    title: str
    error_message: str
    stack_trace: str | None
    file_path: str | None
    service_name: str
    severity: Severity
    tenant_name: str | None
    environment: str  # production, staging
    status: IncidentStatus
    created_at: datetime
    resolved_at: datetime | None = None

class TriageResult(BaseModel):
    incident_id: str
    classification: TriageClassification
    root_cause: str
    service_name: str
    file_path: str | None
    function_name: str | None
    code_snippet: str | None
    suggested_fix: str | None
    confidence: float  # 0.0-1.0
    runbook_reference: str | None  # For INFRA_ISSUE
    manual_steps: list[str] | None  # For INFRA_ISSUE

class FixResult(BaseModel):
    incident_id: str
    file_path: str
    original_code: str
    fixed_code: str
    explanation: str
    diff_summary: str
    iteration: int  # Which CodeRabbit iteration

class ReviewResult(BaseModel):
    passed: bool
    issues: list[dict]
    suggestions: list[str]
    summary: str

class TestResult(BaseModel):
    incident_id: str
    passed: bool
    unit_tests: dict | None
    smoke_tests: dict | None
    duration_ms: int

class VerificationResult(BaseModel):
    incident_id: str
    status: str  # SUCCESS, PARTIAL, FAILED
    message: str
    errors_before: int
    errors_after: int
    monitoring_duration_hours: int
```

---

## Pipeline Orchestration

```python
# agents/orchestrator.py

class PipelineOrchestrator:
    """Coordinates the full incident response pipeline."""

    def __init__(
        self,
        triage_agent: TriageAgent,
        fixer_agent: FixerAgent,
        coderabbit: CodeRabbitService,
        sandbox: SandboxService,
        github: GitHubService,
        pagerduty: PagerDutyService,
        production_monitor: ProductionMonitorService,
        websocket: WebSocketManager
    ):
        self.triage = triage_agent
        self.fixer = fixer_agent
        self.coderabbit = coderabbit
        self.sandbox = sandbox
        self.github = github
        self.pagerduty = pagerduty
        self.monitor = production_monitor
        self.ws = websocket

    async def process_incident(self, incident: Incident) -> None:
        """Run the full pipeline for an incident."""

        try:
            # Acknowledge in PagerDuty
            await self.pagerduty.acknowledge(incident.id)
            await self.ws.broadcast("incident_created", incident)

            # Step 1: Triage
            await self.ws.broadcast("agent_thinking", {
                "step": "triage",
                "message": "Analyzing error with SRE knowledge..."
            })

            triage_result = await self.triage.analyze(incident)
            await self.ws.broadcast("triage_complete", triage_result)

            # Check if fixable
            if triage_result.classification != TriageClassification.FIXABLE:
                await self._handle_non_fixable(incident, triage_result)
                return

            # Step 2: Generate fix with CodeRabbit loop
            fix_result = await self._fix_with_review_loop(incident, triage_result)
            if not fix_result:
                await self._escalate(incident, "Failed to generate valid fix after 3 attempts")
                return

            # Step 3: Sandbox testing
            await self.ws.broadcast("sandbox_status", {"status": "creating"})
            sandbox = await self.sandbox.create_sandbox(incident.id)

            await self.ws.broadcast("sandbox_status", {"status": "applying_fix"})
            await self.sandbox.apply_fix(sandbox, fix_result)

            await self.ws.broadcast("sandbox_status", {"status": "testing"})
            test_result = await self.sandbox.run_tests(sandbox)

            await self.sandbox.cleanup(sandbox)

            if not test_result.passed:
                await self.ws.broadcast("sandbox_status", {"status": "failed"})
                await self._escalate(incident, f"Tests failed: {test_result}")
                return

            await self.ws.broadcast("sandbox_status", {"status": "passed"})

            # Step 4: Create PR
            pr_url = await self.github.create_fix_pr(
                incident, triage_result, fix_result, test_result
            )

            await self.ws.broadcast("incident_resolved", {
                "incident_id": incident.id,
                "pr_url": pr_url
            })

            # Resolve in PagerDuty
            await self.pagerduty.resolve(incident.id, pr_url)

            # Step 5: Monitor production (runs in background after PR merge)
            # This would be triggered by a GitHub webhook when PR is merged

        except Exception as e:
            await self._escalate(incident, f"Pipeline error: {str(e)}")

    async def _fix_with_review_loop(
        self,
        incident: Incident,
        triage: TriageResult
    ) -> FixResult | None:
        """Generate fix with CodeRabbit review loop (max 3 iterations)."""

        coderabbit_feedback = None

        for iteration in range(1, 4):
            await self.ws.broadcast("agent_thinking", {
                "step": "fixing",
                "message": f"Generating fix (attempt {iteration}/3)..."
            })

            fix_result = await self.fixer.generate_fix(triage, coderabbit_feedback)
            fix_result.iteration = iteration

            await self.ws.broadcast("code_diff", fix_result)

            # Run CodeRabbit review
            await self.ws.broadcast("agent_thinking", {
                "step": "review",
                "message": "Running CodeRabbit review..."
            })

            review = await self.coderabbit.review(fix_result)

            if review.passed:
                return fix_result

            # Prepare feedback for next iteration
            coderabbit_feedback = review.summary

            await self.ws.broadcast("review_feedback", {
                "iteration": iteration,
                "issues": review.issues,
                "retrying": iteration < 3
            })

        return None  # Failed after 3 attempts

    async def _handle_non_fixable(
        self,
        incident: Incident,
        triage: TriageResult
    ) -> None:
        """Handle non-fixable issues (infra, transient, needs human)."""

        if triage.classification == TriageClassification.TRANSIENT:
            await self.ws.broadcast("incident_filtered", {
                "incident_id": incident.id,
                "reason": "Transient error - will self-resolve"
            })
            incident.status = IncidentStatus.FILTERED

        elif triage.classification == TriageClassification.INFRA_ISSUE:
            await self.ws.broadcast("incident_escalated", {
                "incident_id": incident.id,
                "reason": f"Infrastructure issue: {triage.root_cause}",
                "runbook": triage.runbook_reference,
                "manual_steps": triage.manual_steps
            })
            await self.pagerduty.escalate(
                incident.id,
                f"Infrastructure issue - see runbook: {triage.runbook_reference}"
            )

        else:  # NEEDS_HUMAN
            await self._escalate(incident, triage.root_cause)

    async def _escalate(self, incident: Incident, reason: str) -> None:
        """Escalate to human on-call."""
        incident.status = IncidentStatus.ESCALATED

        await self.ws.broadcast("incident_escalated", {
            "incident_id": incident.id,
            "reason": reason
        })

        await self.pagerduty.escalate(incident.id, reason)
```

---

## Environment Variables

```bash
# .env

# ═══════════════ AI ═══════════════
ANTHROPIC_API_KEY=sk-ant-...

# ═══════════════ GCP ═══════════════
GOOGLE_APPLICATION_CREDENTIALS=./credentials.json
GCP_PROJECT_ID=your-nucleus-project
GCP_LOG_FILTER=severity>=ERROR

# ═══════════════ GitHub ═══════════════
GITHUB_TOKEN=ghp_...
GITHUB_REPO=your-org/nucleus
GITHUB_BASE_BRANCH=main

# ═══════════════ PagerDuty ═══════════════
PAGERDUTY_ROUTING_KEY=...

# ═══════════════ CodeRabbit ═══════════════
CODERABBIT_MAX_RETRIES=3

# ═══════════════ Sandbox ═══════════════
SANDBOX_TIMEOUT_MINUTES=15
NUCLEUS_REPO_PATH=/Users/sri/nucleus
ONCALL_REPO_PATH=/Users/sri/oncall

# ═══════════════ App ═══════════════
LOG_LEVEL=INFO
DASHBOARD_URL=http://localhost:3000
```

---

## Implementation Order

### Phase 1: Foundation
1. FastAPI scaffolding with health endpoints
2. Pydantic models for all entities
3. WebSocket manager for real-time updates
4. Configuration and environment loading

### Phase 2: Filters
5. Transient error filter (from oncall error patterns)
6. Tenant filter (demo vs production)
7. Error ingestion webhook endpoint

### Phase 3: Triage
8. SRE knowledge loader (from oncall repo)
9. Triage agent with embedded knowledge
10. GCP Cloud Logging integration

### Phase 4: Fix Generation
11. GitHub service (file reading)
12. Fixer agent
13. CodeRabbit service with retry loop

### Phase 5: Validation
14. Sandbox service (Kind cluster management)
15. Test runner integration

### Phase 6: PR & Notification
16. GitHub PR creation
17. PagerDuty integration
18. Production verification service

### Phase 7: Frontend
19. WebSocket hook with auto-reconnect
20. Incident context and state management
21. Dashboard components

---

## Key Reminders

1. **Always reference the repos**: When implementing any component, check the actual code and patterns in `/Users/sri/nucleus` and `/Users/sri/oncall`

2. **Nucleus is Go**: All code fixes target Go code, tests use `go test` and `task test`

3. **Multi-tenant**: Always consider tenant context - demo tenant errors are noise

4. **SRE knowledge**: The oncall repo has valuable runbooks and error patterns - embed them

5. **Production verification**: Don't just create PRs - verify fixes actually work after deployment

6. **Kind clusters**: Use ephemeral Kind clusters for testing - they match Nucleus's local dev environment

7. **Draft PRs**: Always create draft PRs requiring human approval before merge
