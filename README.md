# On Call Helper

AI-powered incident response agent for the Nucleus MDR platform. Automatically triages production errors, generates fixes, and creates PRs - reducing MTTR from hours to minutes.

## Overview

On Call Helper monitors GCP Cloud Logging for production errors in Nucleus services. When an error is detected, it:

1. **Triages** the incident using Claude AI, analyzing stack traces and error patterns
2. **Generates** a code fix based on the root cause analysis
3. **Reviews** the fix using CodeRabbit for quality assurance
4. **Tests** the fix in an isolated sandbox environment
5. **Creates** a pull request with full context and test results
6. **Verifies** the fix in production after merge

All of this happens automatically, with real-time updates visible in the dashboard.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  GCP Cloud      │────>│   On Call       │────>│   GitHub        │
│  Logging        │     │   Helper        │     │   PRs           │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        v                        v                        v
┌───────────────┐      ┌─────────────────┐      ┌───────────────┐
│ Claude AI     │      │ CodeRabbit      │      │ PagerDuty     │
│ (Triage/Fix)  │      │ (Review)        │      │ (Escalation)  │
└───────────────┘      └─────────────────┘      └───────────────┘
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed design documentation.

## Features

- **Intelligent Triage**: Claude AI analyzes errors with SRE knowledge context
- **Automated Fixes**: Generates targeted code fixes for common error patterns
- **Code Review**: CodeRabbit integration ensures fix quality
- **Sandbox Testing**: Isolated Kind clusters test fixes before PR creation
- **Production Verification**: Monitors error rates after deployment
- **Real-time Dashboard**: React dashboard with WebSocket updates
- **Smart Escalation**: Routes non-fixable issues to humans via PagerDuty

## Quick Start

### Prerequisites

- Python 3.9+
- Node.js 18+
- Docker (optional, for containerized deployment)
- Kind (optional, for sandbox testing)

### Setup

```bash
# Clone the repository
git clone https://github.com/your-org/on-call-helper.git
cd on-call-helper

# Run the setup script
./scripts/setup.sh

# Or set up manually:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd frontend
npm install
npm run build
cd ..

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Running Locally

**Backend:**
```bash
source venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

**Frontend (development):**
```bash
cd frontend
npm run dev
```

**With Docker Compose:**
```bash
docker-compose up
```

Access:
- Dashboard: http://localhost:3000
- API: http://localhost:8000
- API Docs: http://localhost:8000/docs

### Demo

Run the demo script to simulate incidents:

```bash
./scripts/demo.sh
```

This will send test incidents to the backend and you can watch the AI process them in real-time on the dashboard.

## Configuration

### Environment Variables

Create a `.env` file with the following:

```bash
# Required for AI features
ANTHROPIC_API_KEY=sk-ant-...

# Required for PR creation
GITHUB_TOKEN=ghp_...
GITHUB_REPO=your-org/nucleus

# Required for error ingestion
GCP_PROJECT_ID=your-gcp-project

# Optional - PagerDuty integration
PAGERDUTY_ROUTING_KEY=...

# Optional - CodeRabbit API (uses default if not set)
CODERABBIT_API_KEY=...

# Repository paths (for SRE knowledge loading)
NUCLEUS_REPO_PATH=/path/to/nucleus
ONCALL_REPO_PATH=/path/to/oncall

# Optional settings
DEBUG=false
LOG_LEVEL=INFO
```

### GCP Cloud Logging Integration

To receive real errors from GCP:

1. Create a Pub/Sub topic for error logs
2. Set up a log sink filtering for ERROR severity
3. Create a push subscription pointing to `https://your-domain/webhook/gcp-logging`

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/live` | GET | Kubernetes liveness probe |
| `/ready` | GET | Kubernetes readiness probe |
| `/info` | GET | App info and configuration |
| `/metrics` | GET | Dashboard metrics |
| `/incidents` | GET | List all incidents |
| `/incidents/{id}` | GET | Get incident details |
| `/webhook/gcp-logging` | POST | GCP Pub/Sub webhook |
| `/webhook/test` | POST | Test webhook (no envelope) |
| `/ws` | WebSocket | Real-time event stream |

### Webhook Example

```bash
# Send a test error
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{
    "error_message": "NullPointerException in caseservice",
    "service_name": "caseservice",
    "severity": "ERROR",
    "stack_trace": "goroutine 1 [running]:\nmain.processCase()\n\t/backend/services/caseservice/handler.go:142"
  }'
```

## Pipeline Stages

Each incident goes through these stages:

| Stage | Description | Outcome |
|-------|-------------|---------|
| **Received** | Error detected from GCP | Incident created |
| **Triaging** | AI analyzes error + context | Classification + root cause |
| **Fixing** | AI generates code fix | Diff with explanation |
| **Reviewing** | CodeRabbit reviews fix | Pass or feedback for retry |
| **Testing** | Sandbox runs test suite | Test results |
| **PR Created** | GitHub PR with full context | PR URL |
| **Verifying** | Monitor production errors | Success/failure |
| **Completed** | Fix verified in production | Incident resolved |

If any stage fails, the incident is escalated to PagerDuty.

## Testing

```bash
# Run all tests
source venv/bin/activate
pytest tests/ -v

# Run specific test file
pytest tests/test_orchestrator.py -v

# Run with coverage
pytest tests/ --cov=backend --cov-report=term-missing

# Run E2E tests
pytest tests/e2e/ -v
```

## Project Structure

```
on-call-helper/
├── backend/
│   ├── main.py                 # FastAPI application
│   ├── config.py               # Environment configuration
│   ├── storage.py              # In-memory incident storage
│   ├── websocket_manager.py    # WebSocket event broadcasting
│   ├── models/
│   │   └── incident.py         # Pydantic data models
│   ├── filters/
│   │   ├── transient.py        # Transient error detection
│   │   └── tenant.py           # Demo tenant filtering
│   ├── services/
│   │   ├── gcp_logging.py      # GCP Pub/Sub webhook
│   │   ├── github.py           # GitHub API (read + PR)
│   │   ├── coderabbit.py       # CodeRabbit API
│   │   ├── sandbox.py          # Kind sandbox management
│   │   ├── pagerduty.py        # PagerDuty integration
│   │   └── production_monitor.py # Production verification
│   ├── agents/
│   │   ├── triage.py           # Triage agent (Claude)
│   │   ├── fixer.py            # Fixer agent (Claude)
│   │   └── orchestrator.py     # Pipeline coordinator
│   └── knowledge/
│       └── loader.py           # SRE knowledge loader
├── frontend/
│   ├── src/
│   │   ├── App.jsx             # Main dashboard
│   │   ├── hooks/
│   │   │   └── useWebSocket.js # WebSocket hook
│   │   ├── context/
│   │   │   └── IncidentContext.jsx # Global state
│   │   └── components/
│   │       ├── MetricsPanel.jsx
│   │       ├── IncidentFeed.jsx
│   │       ├── AgentThinking.jsx
│   │       ├── CodeDiff.jsx
│   │       ├── SandboxStatus.jsx
│   │       └── VerificationStatus.jsx
│   ├── package.json
│   └── vite.config.js
├── tests/
│   ├── test_*.py               # Unit tests
│   └── e2e/                    # End-to-end tests
├── scripts/
│   ├── setup.sh                # Development setup
│   └── demo.sh                 # Demo script
├── docker-compose.yaml         # Full stack deployment
├── Dockerfile                  # Backend container
├── requirements.txt            # Python dependencies
└── .env.example                # Environment template
```

## Metrics

The `/metrics` endpoint provides:

```json
{
  "total_incidents": 150,
  "fixed_count": 120,
  "escalated_count": 30,
  "mttr_seconds": 1800,
  "success_rate": 0.80,
  "by_service": {
    "caseservice": 45,
    "alertservice": 32,
    ...
  },
  "by_classification": {
    "fixable": 120,
    "infra_issue": 15,
    "needs_human": 15
  }
}
```

## Development

### Adding New Services

1. Create service class in `backend/services/`
2. Add to orchestrator initialization
3. Create tests in `tests/test_<service>.py`
4. Update docker-compose if needed

### Adding New Error Patterns

1. Add pattern to transient filter if self-healing
2. Update triage agent prompts if needed
3. Add to tenant filter if demo-related

## Deployment

### Docker

```bash
# Build images
docker-compose build

# Start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Kubernetes

The application includes health endpoints compatible with Kubernetes:

```yaml
livenessProbe:
  httpGet:
    path: /live
    port: 8000
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
```

## Contributing

1. Create a feature branch from `main`
2. Make your changes
3. Run tests: `pytest tests/ -v`
4. Submit a pull request


