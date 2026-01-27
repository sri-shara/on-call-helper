# On Call Helper

AI-powered incident response agent for the Nucleus MDR platform. Automatically triages production errors, generates fixes, and creates PRs.

## Overview

On Call Helper monitors GCP Cloud Logging for production errors in Nucleus services, uses Claude AI to analyze and classify incidents, generates code fixes, and creates pull requests - all automatically.

## Features (Current)

- **Error Ingestion**: Receives errors via GCP Pub/Sub webhook
- **Transient Error Filtering**: Skips self-healing errors (timeouts, rate limits, etc.)
- **Tenant Filtering**: Ignores demo/test tenant errors
- **SRE Knowledge Loading**: Embeds triage procedures and runbooks from oncall repo
- **Metrics Dashboard**: Track incidents, MTTR, success rate

## Quick Start

### Prerequisites

- Python 3.9+
- Virtual environment

### Setup

```bash
# Clone the repo
git clone <repo-url>
cd on-call-helper

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Edit .env with your settings
```

### Run the Server

```bash
# Activate venv
source venv/bin/activate

# Start the server
uvicorn backend.main:app --reload --port 8000
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/live` | GET | Kubernetes liveness probe |
| `/ready` | GET | Kubernetes readiness probe |
| `/info` | GET | App info and configuration |
| `/metrics` | GET | Dashboard metrics |
| `/incidents` | GET | List incidents |
| `/incidents/{id}` | GET | Get incident details |
| `/webhook/gcp-logging` | POST | GCP Pub/Sub webhook |
| `/webhook/test` | POST | Test webhook (no Pub/Sub envelope) |

### Test the Webhook

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

# Check it was created
curl http://localhost:8000/incidents
```

### Check Knowledge Loading

```bash
# Python REPL
source venv/bin/activate
python3

>>> from backend.knowledge import load_sre_knowledge, get_triage_system_prompt
>>> knowledge = load_sre_knowledge()
>>> print(f"Loaded {knowledge.files_loaded} files from {knowledge.loaded_from}")
>>> print(f"Missing: {knowledge.files_missing}")
```

## Running Tests

```bash
# Activate venv
source venv/bin/activate

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_filters.py -v

# Run with coverage
pytest tests/ --cov=backend --cov-report=term-missing
```

## Project Structure

```
on-call-helper/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── config.py            # Environment configuration
│   ├── storage.py           # In-memory storage
│   ├── models/
│   │   └── incident.py      # Pydantic data models
│   ├── filters/
│   │   ├── transient.py     # Transient error detection
│   │   └── tenant.py        # Demo tenant filtering
│   ├── services/
│   │   └── gcp_logging.py   # GCP Pub/Sub webhook handling
│   └── knowledge/
│       └── loader.py        # SRE knowledge loader
├── tests/
│   ├── test_models.py
│   ├── test_storage.py
│   ├── test_health.py
│   ├── test_filters.py
│   ├── test_gcp_logging.py
│   └── test_knowledge_loader.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yaml
└── .env.example
```

## Environment Variables

Key configuration (see `.env.example` for all options):

```bash
# Required for AI features
ANTHROPIC_API_KEY=your-api-key

# GCP (for production webhook)
GCP_PROJECT_ID=your-project

# GitHub (for PR creation)
GITHUB_TOKEN=your-token
GITHUB_REPO=owner/repo

# Repository paths
NUCLEUS_REPO_PATH=/path/to/nucleus
ONCALL_REPO_PATH=/path/to/oncall
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed design documentation.

## PR Plan

See [PR_PLAN.md](PR_PLAN.md) for the implementation roadmap broken into 16 testable PRs.

## Current Status

- [x] PR 1: Project setup and data models
- [x] PR 2: Transient error and tenant filters
- [x] PR 3: GCP Cloud Logging error ingestion
- [x] PR 4: SRE knowledge loader
- [ ] PR 5: Triage agent (in progress)
- [ ] PR 6-16: Remaining features

## Test Coverage

```
138 tests passing
- Models: 16 tests
- Storage: 18 tests
- Health/API: 9 tests
- Filters: 53 tests
- GCP Logging: 24 tests
- Knowledge Loader: 18 tests
```
