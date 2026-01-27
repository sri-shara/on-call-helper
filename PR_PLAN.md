# On Call Helper - PR Implementation Plan

A phased implementation plan broken into testable, reviewable pull requests.

---

## Repository References

> **IMPORTANT**: Always reference these repositories when implementing:
> - **Nucleus**: `/Users/sri/nucleus` - The MDR platform being monitored
> - **On-Call**: `/Users/sri/oncall` - SRE knowledge, runbooks, triage procedures

---

## Overview

```
PR 1: Project Setup & Models
         │
         ▼
PR 2: Filters (Transient + Tenant)
         │
         ▼
PR 3: Error Ingestion (GCP Webhook)
         │
         ▼
PR 4: SRE Knowledge Loader
         │
         ▼
PR 5: Triage Agent
         │
         ▼
PR 6: GitHub Service (Read)
         │
         ▼
PR 7: Fixer Agent
         │
         ▼
PR 8: CodeRabbit Service
         │
         ▼
PR 9: Sandbox Service (Kind)
         │
         ▼
PR 10: GitHub Service (PR Creation)
         │
         ▼
PR 11: PagerDuty Service
         │
         ▼
PR 12: Production Verification
         │
         ▼
PR 13: Pipeline Orchestrator
         │
         ▼
PR 14: WebSocket & Real-time Events
         │
         ▼
PR 15: React Dashboard
         │
         ▼
PR 16: End-to-End Integration
```

---

## PR 1: Project Setup & Data Models

**Branch**: `feat/project-setup`

**Description**: Initialize the project with FastAPI scaffolding, configuration management, and all Pydantic data models.

### Files to Create

```
backend/
├── __init__.py
├── main.py                 # FastAPI app with health endpoints
├── config.py               # Environment configuration
├── models/
│   ├── __init__.py
│   └── incident.py         # All Pydantic models
└── storage.py              # In-memory storage stub
requirements.txt
.env.example
.gitignore
Dockerfile
docker-compose.yaml
```

### Acceptance Criteria

- [ ] `uvicorn backend.main:app` starts without errors
- [ ] `GET /health` returns `{"status": "healthy"}`
- [ ] `GET /health/ready` returns `{"status": "ready"}`
- [ ] All models can be instantiated and serialized to JSON
- [ ] Environment variables load correctly from `.env`
- [ ] Docker build succeeds: `docker build -t oncall-helper .`

### Test Commands

```bash
# Start the server
uvicorn backend.main:app --reload

# Test health endpoints
curl http://localhost:8000/health
curl http://localhost:8000/health/ready

# Run unit tests
pytest tests/test_models.py -v
```

### Key Code

```python
# backend/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="On Call Helper", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/health/ready")
async def ready():
    return {"status": "ready"}
```

---

## PR 2: Filters (Transient + Tenant)

**Branch**: `feat/filters`

**Depends on**: PR 1

**Description**: Implement error filtering to skip transient/self-healing errors and demo tenant noise.

### Reference

- `/Users/sri/oncall/.claude/commands/sre-triage/error-patterns.md` - Known transient patterns
- `/Users/sri/oncall/.claude/commands/sre-triage/tenant-reference.md` - Tenant classification

### Files to Create

```
backend/
└── filters/
    ├── __init__.py
    ├── transient.py        # Self-healing error detection
    └── tenant.py           # Demo vs production tenant filter
tests/
└── test_filters.py
```

### Acceptance Criteria

- [ ] `is_transient_error("Routing deadline expired")` returns `(True, "reason")`
- [ ] `is_transient_error("NullPointerException")` returns `(False, "")`
- [ ] `should_process_tenant("TENEX Demo")` returns `(False, "Demo tenant")`
- [ ] `should_process_tenant("Whitney")` returns `(True, "Production tenant")`
- [ ] All patterns from oncall repo's error-patterns.md are covered
- [ ] All tenants from oncall repo's tenant-reference.md are classified

### Test Commands

```bash
pytest tests/test_filters.py -v

# Expected output:
# test_transient_routing_deadline_expired PASSED
# test_transient_context_deadline PASSED
# test_transient_case_number_exists PASSED
# test_not_transient_null_pointer PASSED
# test_demo_tenant_tenex PASSED
# test_production_tenant_whitney PASSED
```

### Key Code

```python
# backend/filters/transient.py
TRANSIENT_PATTERNS = [
    {"pattern": r"Routing deadline expired", "reason": "Auto-retries"},
    {"pattern": r"context deadline exceeded", "reason": "Timeout with retry"},
    {"pattern": r"case number already exists", "reason": "Race condition, retries"},
    # ... more patterns from oncall repo
]

def is_transient_error(error_message: str) -> tuple[bool, str]:
    for p in TRANSIENT_PATTERNS:
        if re.search(p["pattern"], error_message, re.IGNORECASE):
            return True, p["reason"]
    return False, ""
```

---

## PR 3: Error Ingestion (GCP Webhook)

**Branch**: `feat/error-ingestion`

**Depends on**: PR 2

**Description**: Implement GCP Cloud Logging webhook endpoint to receive production errors.

### Files to Create/Modify

```
backend/
├── main.py                 # Add webhook endpoint
└── services/
    ├── __init__.py
    └── gcp_logging.py      # Log parsing and incident creation
tests/
├── test_gcp_logging.py
└── fixtures/
    └── sample_gcp_logs.json
```

### Acceptance Criteria

- [ ] `POST /webhook/gcp-logs` accepts Pub/Sub push messages
- [ ] GCP log entry is parsed correctly (textPayload, jsonPayload)
- [ ] Incident ID generated in format `OCH-{8chars}`
- [ ] Transient errors return `{"status": "filtered", "reason": "..."}`
- [ ] Demo tenant errors return `{"status": "filtered", "reason": "..."}`
- [ ] Valid errors return `{"status": "processing", "incident_id": "..."}`
- [ ] Incident stored in storage module

### Test Commands

```bash
# Run unit tests
pytest tests/test_gcp_logging.py -v

# Manual test with sample payload
curl -X POST http://localhost:8000/webhook/gcp-logs \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_gcp_logs.json

# Test filtering
curl -X POST http://localhost:8000/webhook/gcp-logs \
  -H "Content-Type: application/json" \
  -d '{"message": {"data": "base64_encoded_transient_error"}}'
```

### Key Code

```python
# backend/main.py
@app.post("/webhook/gcp-logs")
async def receive_gcp_log(request: Request):
    log_entry = await gcp_logging.parse_pubsub_message(request)

    # Apply filters
    is_transient, reason = is_transient_error(log_entry.error_message)
    if is_transient:
        return {"status": "filtered", "reason": reason}

    should_process, reason = should_process_tenant(log_entry.tenant_name)
    if not should_process:
        return {"status": "filtered", "reason": reason}

    # Create incident
    incident = gcp_logging.create_incident(log_entry)
    storage.save_incident(incident)

    return {"status": "processing", "incident_id": incident.id}
```

---

## PR 4: SRE Knowledge Loader

**Branch**: `feat/sre-knowledge`

**Depends on**: PR 1

**Description**: Load SRE knowledge from the oncall repository for embedding in the triage agent.

### Reference

- `/Users/sri/oncall/.claude/commands/sre-triage.md`
- `/Users/sri/oncall/.claude/commands/sre-triage/*`
- `/Users/sri/oncall/runbooks/*`

### Files to Create

```
backend/
└── knowledge/
    ├── __init__.py
    └── loader.py           # Load knowledge from oncall repo
tests/
└── test_knowledge_loader.py
```

### Acceptance Criteria

- [ ] `load_sre_knowledge()` returns dict with all knowledge areas
- [ ] Triage framework loaded from `sre-triage.md`
- [ ] Infrastructure checks loaded from `infrastructure-checks.md`
- [ ] All runbooks loaded (alloydb, pubsub, cloud-run, integrations)
- [ ] Error patterns loaded from `error-patterns.md`
- [ ] Tenant reference loaded from `tenant-reference.md`
- [ ] Missing files handled gracefully (return placeholder text)
- [ ] Knowledge can be formatted as a single prompt string

### Test Commands

```bash
pytest tests/test_knowledge_loader.py -v

# Verify all knowledge loaded
python -c "
from backend.knowledge.loader import load_sre_knowledge
k = load_sre_knowledge()
print('Loaded sections:', list(k.keys()))
print('Runbooks:', list(k['runbooks'].keys()))
print('Triage framework length:', len(k['triage_framework']))
"
```

### Key Code

```python
# backend/knowledge/loader.py
from pathlib import Path

ONCALL_REPO_PATH = Path("/Users/sri/oncall")

def load_sre_knowledge() -> dict:
    return {
        "triage_framework": _load(ONCALL_REPO_PATH / ".claude/commands/sre-triage.md"),
        "infrastructure_checks": _load(ONCALL_REPO_PATH / ".claude/commands/sre-triage/infrastructure-checks.md"),
        "error_patterns": _load(ONCALL_REPO_PATH / ".claude/commands/sre-triage/error-patterns.md"),
        "tenant_reference": _load(ONCALL_REPO_PATH / ".claude/commands/sre-triage/tenant-reference.md"),
        "runbooks": {
            "alloydb": _load(ONCALL_REPO_PATH / "runbooks/alloydb.md"),
            "pubsub": _load(ONCALL_REPO_PATH / "runbooks/pubsub-backlogs.md"),
            "cloud_run": _load(ONCALL_REPO_PATH / "runbooks/cloud-run.md"),
            "integrations": _load(ONCALL_REPO_PATH / "runbooks/integrations.md"),
        }
    }

def _load(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return f"[Knowledge file not found: {path}]"
```

---

## PR 5: Triage Agent

**Branch**: `feat/triage-agent`

**Depends on**: PR 4

**Description**: Implement Claude-based triage agent with embedded SRE knowledge.

### Files to Create

```
backend/
└── agents/
    ├── __init__.py
    └── triage.py           # Claude triage agent
tests/
├── test_triage_agent.py
└── fixtures/
    └── sample_incidents.py
```

### Acceptance Criteria

- [ ] System prompt includes all SRE knowledge from PR 4
- [ ] Agent classifies errors as: FIXABLE, INFRA_ISSUE, TRANSIENT, NEEDS_HUMAN
- [ ] FIXABLE results include: root_cause, file_path, function_name, confidence
- [ ] INFRA_ISSUE results include: runbook_reference, manual_steps
- [ ] Confidence score between 0.0 and 1.0
- [ ] Response parsed correctly from Claude JSON output
- [ ] Handles Claude API errors gracefully

### Test Commands

```bash
pytest tests/test_triage_agent.py -v

# Integration test with real Claude API
python -c "
from backend.agents.triage import TriageAgent
from backend.models.incident import Incident
import asyncio

agent = TriageAgent()
incident = Incident(
    id='OCH-TEST001',
    title='NullPointerException in caseservice',
    error_message='panic: runtime error: invalid memory address',
    stack_trace='goroutine 1 [running]:\nmain.processCase()\n\t/backend/services/caseservice/handler.go:142',
    service_name='caseservice',
    severity='high',
    environment='production'
)
result = asyncio.run(agent.analyze(incident))
print(f'Classification: {result.classification}')
print(f'Root cause: {result.root_cause}')
print(f'Confidence: {result.confidence}')
"
```

### Key Code

```python
# backend/agents/triage.py
from anthropic import Anthropic
from backend.knowledge.loader import load_sre_knowledge

class TriageAgent:
    def __init__(self):
        self.client = Anthropic()
        self.knowledge = load_sre_knowledge()
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        return f"""
You are an SRE triage agent for Nucleus, an MDR platform.

## Triage Framework
{self.knowledge["triage_framework"]}

## Infrastructure Checks
{self.knowledge["infrastructure_checks"]}

## Known Error Patterns
{self.knowledge["error_patterns"]}

## Runbooks
{self._format_runbooks()}

Classify the error and provide analysis as JSON.
"""

    async def analyze(self, incident: Incident) -> TriageResult:
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=self.system_prompt,
            messages=[{"role": "user", "content": self._format_incident(incident)}]
        )
        return self._parse_response(response, incident.id)
```

---

## PR 6: GitHub Service (Read Operations)

**Branch**: `feat/github-read`

**Depends on**: PR 1

**Description**: Implement GitHub service for reading source files from the Nucleus repository.

### Reference

- `/Users/sri/nucleus/backend/services/` - Nucleus service structure

### Files to Create

```
backend/
└── services/
    └── github.py           # GitHub API client (read operations)
tests/
└── test_github_read.py
```

### Acceptance Criteria

- [ ] `get_file_content(repo, path, ref)` returns file content as string
- [ ] Works with both public and private repos (using token)
- [ ] Returns `None` for non-existent files (doesn't crash)
- [ ] Handles rate limiting gracefully
- [ ] Can read Go files from Nucleus repo structure

### Test Commands

```bash
pytest tests/test_github_read.py -v

# Integration test
python -c "
from backend.services.github import GitHubService
import asyncio

gh = GitHubService(token='your-token', repo='your-org/nucleus')
content = asyncio.run(gh.get_file_content('backend/services/caseservice/handler.go'))
print(f'File length: {len(content)} chars')
print(f'First 200 chars: {content[:200]}')
"
```

---

## PR 7: Fixer Agent

**Branch**: `feat/fixer-agent`

**Depends on**: PR 5, PR 6

**Description**: Implement Claude-based code fix generation agent.

### Reference

- `/Users/sri/nucleus/backend/` - Nucleus code patterns and style

### Files to Create

```
backend/
└── agents/
    └── fixer.py            # Claude fixer agent
tests/
├── test_fixer_agent.py
└── fixtures/
    └── sample_triage_results.py
```

### Acceptance Criteria

- [ ] Agent reads actual source from GitHub before generating fix
- [ ] Fix is minimal (doesn't refactor unrelated code)
- [ ] Output includes: original_code, fixed_code, explanation, diff_summary
- [ ] Generated Go code compiles (syntax check)
- [ ] Handles CodeRabbit feedback for retry iterations
- [ ] Iteration count tracked (1-3)

### Test Commands

```bash
pytest tests/test_fixer_agent.py -v

# Integration test
python -c "
from backend.agents.fixer import FixerAgent
from backend.services.github import GitHubService
import asyncio

gh = GitHubService(token='token', repo='org/nucleus')
fixer = FixerAgent(github_service=gh)

triage = TriageResult(
    incident_id='OCH-TEST001',
    classification='fixable',
    root_cause='Nil pointer dereference when case is None',
    file_path='backend/services/caseservice/handler.go',
    function_name='processCase',
    code_snippet='case.ID',
    confidence=0.85
)

fix = asyncio.run(fixer.generate_fix(triage))
print(f'Fixed code preview: {fix.fixed_code[:200]}')
"
```

---

## PR 8: CodeRabbit Service

**Branch**: `feat/coderabbit`

**Depends on**: PR 7

**Description**: Implement CodeRabbit CLI integration for automated code review.

### Files to Create

```
backend/
└── services/
    └── coderabbit.py       # CodeRabbit CLI wrapper
tests/
└── test_coderabbit.py
```

### Acceptance Criteria

- [ ] Writes fix to temp file with correct extension (.go)
- [ ] Runs `coderabbit review <file> --format json`
- [ ] Parses JSON output for issues
- [ ] Identifies blocking issues (critical, high, security)
- [ ] `review.passed` is True when no blocking issues
- [ ] Formats feedback for Claude retry loop
- [ ] Handles CLI not installed gracefully
- [ ] Cleans up temp files

### Test Commands

```bash
# Verify CodeRabbit CLI installed
coderabbit --version
coderabbit auth status

pytest tests/test_coderabbit.py -v

# Manual test
python -c "
from backend.services.coderabbit import CodeRabbitService
import asyncio

cr = CodeRabbitService()
fix = FixResult(
    incident_id='test',
    file_path='test.go',
    original_code='func test() {}',
    fixed_code='func test() { return nil }',
    explanation='Added return',
    diff_summary='Added return statement'
)
result = asyncio.run(cr.review(fix))
print(f'Passed: {result.passed}')
print(f'Issues: {result.issues}')
"
```

---

## PR 9: Sandbox Service (Kind Clusters)

**Branch**: `feat/sandbox`

**Depends on**: PR 1

**Description**: Implement ephemeral Kind cluster management for testing fixes.

### Reference

- `/Users/sri/nucleus/localdev/` - Kind configuration
- `/Users/sri/nucleus/Tiltfile` - Deployment setup
- `/Users/sri/nucleus/Taskfile.yaml` - Test commands

### Files to Create

```
backend/
└── services/
    └── sandbox.py          # Kind cluster management
sandbox/
├── kind-config.yaml        # Kind cluster config
├── deploy.sh               # Deploy script
├── run-tests.sh            # Test runner
└── cleanup.sh              # Cleanup script
tests/
└── test_sandbox.py
```

### Acceptance Criteria

- [ ] `create_sandbox()` creates Kind cluster with unique name
- [ ] Cluster created within 3 minutes
- [ ] `apply_fix()` clones Nucleus, applies patch, builds images
- [ ] `run_tests()` executes `task test` and captures output
- [ ] `run_tests()` executes smoke tests if unit tests pass
- [ ] `cleanup()` deletes cluster and temp directories
- [ ] Handles cluster creation failures gracefully
- [ ] Timeout handling for long-running tests

### Test Commands

```bash
# Verify Kind installed
kind --version

pytest tests/test_sandbox.py -v

# Manual sandbox test (creates real cluster)
python -c "
from backend.services.sandbox import SandboxService
import asyncio

sandbox_svc = SandboxService()

# Create
sandbox = asyncio.run(sandbox_svc.create_sandbox('TEST001'))
print(f'Created cluster: {sandbox.id}')

# Cleanup
asyncio.run(sandbox_svc.cleanup(sandbox))
print('Cleaned up')
"
```

### Key Code

```python
# sandbox/deploy.sh
#!/bin/bash
set -e

CLUSTER_NAME=$1
WORK_DIR=$2

# Switch kubectl context
kubectl config use-context kind-$CLUSTER_NAME

# Deploy using Helm (simplified, no Tilt)
helm install nucleus ./k8s/charts/nucleus \
  --namespace default \
  --set image.tag=local \
  --wait --timeout 5m
```

---

## PR 10: GitHub Service (PR Creation)

**Branch**: `feat/github-pr`

**Depends on**: PR 6

**Description**: Extend GitHub service to create branches and pull requests.

### Files to Modify

```
backend/
└── services/
    └── github.py           # Add PR creation methods
tests/
└── test_github_pr.py
```

### Acceptance Criteria

- [ ] `create_branch()` creates branch from main
- [ ] `update_file()` commits fix to branch
- [ ] `create_pull_request()` creates draft PR
- [ ] PR body includes: incident details, root cause, code diff, test results
- [ ] PR labeled with: oncall-helper, auto-fix, tests-passed
- [ ] Returns PR URL
- [ ] Handles branch already exists error

### Test Commands

```bash
pytest tests/test_github_pr.py -v

# Integration test (creates real PR in test repo)
python -c "
from backend.services.github import GitHubService
import asyncio

gh = GitHubService(token='token', repo='org/test-repo')
pr_url = asyncio.run(gh.create_fix_pr(
    incident=mock_incident,
    triage=mock_triage,
    fix=mock_fix,
    test_results=mock_results
))
print(f'PR created: {pr_url}')
"
```

---

## PR 11: PagerDuty Service

**Branch**: `feat/pagerduty`

**Depends on**: PR 1

**Description**: Implement PagerDuty Events API v2 integration.

### Reference

- `/Users/sri/oncall/README.md` - PagerDuty usage in on-call workflow

### Files to Create

```
backend/
└── services/
    └── pagerduty.py        # PagerDuty Events API
tests/
└── test_pagerduty.py
```

### Acceptance Criteria

- [ ] `trigger()` creates PagerDuty incident with correct severity
- [ ] `acknowledge()` acknowledges incident
- [ ] `resolve()` resolves with PR link in custom details
- [ ] `escalate()` triggers high-severity escalation incident
- [ ] Dedup key uses incident ID (prevents duplicates)
- [ ] Custom details include: service, error message, dashboard URL
- [ ] Handles API errors gracefully (fire-and-forget for non-critical)

### Test Commands

```bash
pytest tests/test_pagerduty.py -v

# Integration test (sends to real PagerDuty)
python -c "
from backend.services.pagerduty import PagerDutyService
import asyncio

pd = PagerDutyService(routing_key='your-key')

# Trigger test incident
dedup = asyncio.run(pd.trigger(mock_incident))
print(f'Triggered: {dedup}')

# Acknowledge
asyncio.run(pd.acknowledge(mock_incident.id))
print('Acknowledged')

# Resolve
asyncio.run(pd.resolve(mock_incident.id, 'https://github.com/pr/123'))
print('Resolved')
"
```

---

## PR 12: Production Verification Service

**Branch**: `feat/production-verification`

**Depends on**: PR 3

**Description**: Monitor production after fix deployment to verify resolution.

### Files to Create

```
backend/
└── services/
    └── production_monitor.py   # Post-deploy verification
tests/
└── test_production_monitor.py
```

### Acceptance Criteria

- [ ] Builds Cloud Logging filter for specific error signature
- [ ] Monitors for configurable duration (default 2 hours)
- [ ] Checks error count at regular intervals (default 5 min)
- [ ] Compares post-deploy errors to pre-deploy baseline
- [ ] Returns SUCCESS if errors drop to 0 or >90% reduction
- [ ] Returns PARTIAL if errors reduced but not eliminated
- [ ] Returns FAILED if errors persist or increase
- [ ] Broadcasts status updates via callback

### Test Commands

```bash
pytest tests/test_production_monitor.py -v

# Integration test
python -c "
from backend.services.production_monitor import ProductionMonitorService
import asyncio
from datetime import datetime

monitor = ProductionMonitorService(project_id='your-project')

result = asyncio.run(monitor.verify_fix(
    incident=mock_incident,
    pr_merged_at=datetime.utcnow()
))
print(f'Status: {result.status}')
print(f'Message: {result.message}')
"
```

---

## PR 13: Pipeline Orchestrator

**Branch**: `feat/orchestrator`

**Depends on**: PR 5, PR 7, PR 8, PR 9, PR 10, PR 11, PR 12

**Description**: Implement the main pipeline that coordinates all components.

### Files to Create

```
backend/
└── agents/
    └── orchestrator.py     # Pipeline coordinator
tests/
└── test_orchestrator.py
```

### Acceptance Criteria

- [ ] Receives incident and runs full pipeline
- [ ] Triage → Fix → Review → Test → PR flow works
- [ ] CodeRabbit retry loop (max 3 iterations)
- [ ] Non-fixable incidents handled (INFRA_ISSUE, TRANSIENT, NEEDS_HUMAN)
- [ ] Escalation triggered on pipeline failures
- [ ] All steps emit status events (for WebSocket)
- [ ] Pipeline handles partial failures gracefully
- [ ] Concurrent incidents don't interfere

### Test Commands

```bash
pytest tests/test_orchestrator.py -v

# Integration test (runs full pipeline)
python -c "
from backend.agents.orchestrator import PipelineOrchestrator
import asyncio

orchestrator = PipelineOrchestrator(
    triage_agent=triage,
    fixer_agent=fixer,
    coderabbit=coderabbit,
    sandbox=sandbox,
    github=github,
    pagerduty=pagerduty,
    production_monitor=monitor,
    websocket=None  # No WS for test
)

asyncio.run(orchestrator.process_incident(mock_incident))
"
```

---

## PR 14: WebSocket & Real-time Events

**Branch**: `feat/websocket`

**Depends on**: PR 13

**Description**: Implement WebSocket manager for real-time dashboard updates.

### Files to Create/Modify

```
backend/
├── main.py                 # Add WebSocket endpoint
└── websocket_manager.py    # Connection management
tests/
└── test_websocket.py
```

### Acceptance Criteria

- [ ] `GET /ws` accepts WebSocket connections
- [ ] Welcome message sent on connect with current metrics
- [ ] `broadcast()` sends event to all connected clients
- [ ] Disconnected clients cleaned up automatically
- [ ] Event types: incident_created, agent_thinking, triage_complete, code_diff, sandbox_status, incident_resolved, incident_escalated
- [ ] Events include timestamp
- [ ] Connection metadata tracked (client ID, connected_at)

### Test Commands

```bash
pytest tests/test_websocket.py -v

# Manual WebSocket test
websocat ws://localhost:8000/ws

# Or with Python
python -c "
import asyncio
import websockets

async def test():
    async with websockets.connect('ws://localhost:8000/ws') as ws:
        msg = await ws.recv()
        print(f'Welcome: {msg}')

asyncio.run(test())
"
```

---

## PR 15: React Dashboard

**Branch**: `feat/dashboard`

**Depends on**: PR 14

**Description**: Implement React dashboard for visualizing incidents and pipeline status.

### Files to Create

```
frontend/
├── package.json
├── src/
│   ├── App.jsx
│   ├── index.jsx
│   ├── hooks/
│   │   └── useWebSocket.js     # WebSocket with auto-reconnect
│   ├── context/
│   │   └── IncidentContext.jsx # Global state from events
│   └── components/
│       ├── IncidentFeed.jsx    # Incident list
│       ├── AgentThinking.jsx   # Real-time agent activity
│       ├── CodeDiff.jsx        # Before/after comparison
│       ├── SandboxStatus.jsx   # Test progress
│       ├── MetricsPanel.jsx    # MTTR, success rate
│       └── VerificationStatus.jsx  # Production verification
├── public/
│   └── index.html
└── vite.config.js
```

### Acceptance Criteria

- [ ] Dashboard connects to WebSocket on load
- [ ] Auto-reconnects on disconnect
- [ ] Incidents displayed in real-time feed
- [ ] Agent thinking steps shown with timestamps
- [ ] Code diff rendered with syntax highlighting
- [ ] Sandbox status shows: creating → testing → passed/failed
- [ ] Metrics panel shows: total incidents, fixed, escalated, MTTR
- [ ] Production verification status visible post-merge
- [ ] Responsive design (works on mobile)

### Test Commands

```bash
cd frontend
npm install
npm run dev

# Open http://localhost:5173
# Verify WebSocket connection in browser console
# Trigger test incident and watch dashboard update
```

---

## PR 16: End-to-End Integration

**Branch**: `feat/e2e-integration`

**Depends on**: All previous PRs

**Description**: Final integration, documentation, and end-to-end testing.

### Files to Create/Modify

```
README.md                   # Project documentation
docker-compose.yaml         # Full stack compose
tests/
└── e2e/
    ├── test_full_pipeline.py
    └── test_dashboard_e2e.py
scripts/
├── setup.sh                # Initial setup script
└── demo.sh                 # Demo script for presentations
```

### Acceptance Criteria

- [ ] `docker-compose up` starts full stack (backend + frontend)
- [ ] GCP webhook receives test error → full pipeline runs
- [ ] Dashboard shows real-time updates throughout
- [ ] PR created in GitHub (draft)
- [ ] PagerDuty notifications sent
- [ ] Production verification runs after mock deployment
- [ ] README includes setup instructions, architecture, usage
- [ ] Demo script simulates incident for presentations

### Test Commands

```bash
# Start full stack
docker-compose up -d

# Run E2E tests
pytest tests/e2e/ -v

# Demo
./scripts/demo.sh

# Check all components healthy
curl http://localhost:8000/health
curl http://localhost:5173  # Dashboard loads
```

---

## PR Checklist Template

Use this checklist for each PR:

```markdown
## PR Checklist

### Code Quality
- [ ] Code follows project style guidelines
- [ ] No hardcoded secrets or credentials
- [ ] Error handling is comprehensive
- [ ] Logging added for debugging

### Testing
- [ ] Unit tests added/updated
- [ ] All tests pass locally
- [ ] Integration tests pass (if applicable)

### Documentation
- [ ] Code comments where necessary
- [ ] README updated (if needed)
- [ ] ARCHITECTURE.md referenced

### Review
- [ ] Self-reviewed the code
- [ ] Checked against acceptance criteria
- [ ] Tested manually
```

---

## Estimated Effort

| PR | Complexity | Dependencies |
|----|------------|--------------|
| PR 1: Project Setup | Low | None |
| PR 2: Filters | Low | PR 1 |
| PR 3: Error Ingestion | Medium | PR 2 |
| PR 4: SRE Knowledge | Low | PR 1 |
| PR 5: Triage Agent | Medium | PR 4 |
| PR 6: GitHub Read | Low | PR 1 |
| PR 7: Fixer Agent | Medium | PR 5, PR 6 |
| PR 8: CodeRabbit | Medium | PR 7 |
| PR 9: Sandbox | High | PR 1 |
| PR 10: GitHub PR | Medium | PR 6 |
| PR 11: PagerDuty | Low | PR 1 |
| PR 12: Production Monitor | Medium | PR 3 |
| PR 13: Orchestrator | High | Many |
| PR 14: WebSocket | Medium | PR 13 |
| PR 15: Dashboard | Medium | PR 14 |
| PR 16: E2E Integration | Medium | All |

---

## Key Reminders

1. **Always reference the repos** when implementing:
   - `/Users/sri/nucleus` - Nucleus codebase
   - `/Users/sri/oncall` - SRE knowledge and runbooks

2. **Test at each PR** - Don't wait until the end to test

3. **Keep PRs focused** - Each PR should be independently reviewable

4. **Update ARCHITECTURE.md** - If implementation differs from design
