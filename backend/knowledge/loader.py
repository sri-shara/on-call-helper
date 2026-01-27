"""
SRE Knowledge Loader for On Call Helper.

Loads SRE knowledge from the oncall repository for embedding in the
triage agent's system prompt.

Repository Reference:
- /Users/sri/oncall - The on-call handbook with runbooks and triage procedures
"""

from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel

from backend.config import settings


class SREKnowledge(BaseModel):
    """Container for all SRE knowledge loaded from the oncall repo."""

    # Main triage framework
    triage_framework: str

    # Triage sub-components
    infrastructure_checks: str
    bigquery_queries: str
    tenant_reference: str
    error_patterns: str
    output_format: str
    handoff_updates: str

    # Runbooks
    runbook_alloydb: str
    runbook_pubsub: str
    runbook_cloud_run: str
    runbook_integrations: str
    runbook_secops: str

    # Metadata
    loaded_from: str
    files_loaded: int
    files_missing: int


def _load_file(path: Path) -> str:
    """
    Load file content, return placeholder if not found.

    Args:
        path: Path to the file

    Returns:
        File content or placeholder message
    """
    try:
        content = path.read_text(encoding="utf-8")
        return content.strip()
    except FileNotFoundError:
        return f"[Knowledge file not found: {path.name}]"
    except PermissionError:
        return f"[Permission denied: {path.name}]"
    except Exception as e:
        return f"[Error loading {path.name}: {e}]"


def _is_loaded(content: str) -> bool:
    """Check if content was successfully loaded (not a placeholder)."""
    return not content.startswith("[")


@lru_cache(maxsize=1)
def load_sre_knowledge(oncall_path: Optional[str] = None) -> SREKnowledge:
    """
    Load all SRE knowledge from the oncall repository.

    This function is cached - subsequent calls return the same instance.

    Args:
        oncall_path: Optional override for oncall repo path

    Returns:
        SREKnowledge object containing all loaded knowledge
    """
    base_path = Path(oncall_path) if oncall_path else settings.oncall_repo_path

    # Paths to knowledge files
    triage_base = base_path / ".claude" / "commands"
    triage_subdir = triage_base / "sre-triage"
    runbooks_dir = base_path / "runbooks"

    # Load all files
    knowledge_files = {
        # Main triage framework
        "triage_framework": triage_base / "sre-triage.md",

        # Triage sub-components
        "infrastructure_checks": triage_subdir / "infrastructure-checks.md",
        "bigquery_queries": triage_subdir / "bigquery-queries.md",
        "tenant_reference": triage_subdir / "tenant-reference.md",
        "error_patterns": triage_subdir / "error-patterns.md",
        "output_format": triage_subdir / "output-format.md",
        "handoff_updates": triage_subdir / "handoff-updates.md",

        # Runbooks
        "runbook_alloydb": runbooks_dir / "alloydb.md",
        "runbook_pubsub": runbooks_dir / "pubsub-backlogs.md",
        "runbook_cloud_run": runbooks_dir / "cloud-run.md",
        "runbook_integrations": runbooks_dir / "integrations.md",
        "runbook_secops": runbooks_dir / "secops-integration.md",
    }

    # Load all files
    loaded_content = {}
    files_loaded = 0
    files_missing = 0

    for key, path in knowledge_files.items():
        content = _load_file(path)
        loaded_content[key] = content
        if _is_loaded(content):
            files_loaded += 1
        else:
            files_missing += 1

    return SREKnowledge(
        triage_framework=loaded_content["triage_framework"],
        infrastructure_checks=loaded_content["infrastructure_checks"],
        bigquery_queries=loaded_content["bigquery_queries"],
        tenant_reference=loaded_content["tenant_reference"],
        error_patterns=loaded_content["error_patterns"],
        output_format=loaded_content["output_format"],
        handoff_updates=loaded_content["handoff_updates"],
        runbook_alloydb=loaded_content["runbook_alloydb"],
        runbook_pubsub=loaded_content["runbook_pubsub"],
        runbook_cloud_run=loaded_content["runbook_cloud_run"],
        runbook_integrations=loaded_content["runbook_integrations"],
        runbook_secops=loaded_content["runbook_secops"],
        loaded_from=str(base_path),
        files_loaded=files_loaded,
        files_missing=files_missing,
    )


def get_triage_system_prompt(knowledge: Optional[SREKnowledge] = None) -> str:
    """
    Build the system prompt for the triage agent with embedded SRE knowledge.

    Args:
        knowledge: Optional pre-loaded knowledge, loads if not provided

    Returns:
        Complete system prompt string for Claude
    """
    if knowledge is None:
        knowledge = load_sre_knowledge()

    return f'''You are an SRE triage agent for Nucleus, a Security Operations Center (SOC) platform
for Managed Detection & Response (MDR) services.

Your task is to analyze production errors and determine:
1. Classification: FIXABLE (code bug), INFRA_ISSUE (infrastructure), TRANSIENT (self-healing), or NEEDS_HUMAN
2. Root cause analysis
3. Recommended actions

## Your Knowledge Base

### Triage Framework
{knowledge.triage_framework}

### Infrastructure Checks
{knowledge.infrastructure_checks}

### Known Error Patterns
{knowledge.error_patterns}

### Tenant Reference
{knowledge.tenant_reference}

## Runbooks

### AlloyDB Issues
{knowledge.runbook_alloydb}

### Pub/Sub Issues
{knowledge.runbook_pubsub}

### Cloud Run Issues
{knowledge.runbook_cloud_run}

### Integration Issues
{knowledge.runbook_integrations}

### SecOps Integration Issues
{knowledge.runbook_secops}

## Output Format

You must respond with a JSON object containing your analysis:

```json
{{
    "classification": "FIXABLE" | "INFRA_ISSUE" | "TRANSIENT" | "NEEDS_HUMAN",
    "confidence": 0.0-1.0,
    "root_cause": "Detailed explanation of what went wrong",

    // For FIXABLE classification:
    "service_name": "affected service",
    "file_path": "path/to/buggy/file.go",
    "function_name": "functionWithBug",
    "code_snippet": "problematic code excerpt",
    "suggested_fix": "high-level description of fix needed",

    // For INFRA_ISSUE classification:
    "runbook_reference": "runbooks/alloydb.md",
    "manual_steps": ["step 1", "step 2"],

    // For all classifications:
    "related_context": ["additional observations"]
}}
```

## Important Guidelines

1. **Be specific**: Don't just say "database error" - identify which database, what operation, why it failed
2. **Check patterns first**: Many errors are known transient patterns that self-resolve
3. **Consider tenant context**: Demo tenant errors are usually noise
4. **Reference runbooks**: If it's an infrastructure issue, point to the relevant runbook
5. **Confidence score**: Be honest about certainty. Use 0.9+ only for clear-cut cases
6. **Minimal fixes**: For FIXABLE issues, suggest the smallest change that fixes the problem
'''


def get_knowledge_summary(knowledge: Optional[SREKnowledge] = None) -> Dict[str, str]:
    """
    Get a summary of loaded knowledge for debugging/display.

    Returns:
        Dictionary with file names and their load status
    """
    if knowledge is None:
        knowledge = load_sre_knowledge()

    return {
        "loaded_from": knowledge.loaded_from,
        "files_loaded": knowledge.files_loaded,
        "files_missing": knowledge.files_missing,
        "triage_framework": "loaded" if _is_loaded(knowledge.triage_framework) else "missing",
        "infrastructure_checks": "loaded" if _is_loaded(knowledge.infrastructure_checks) else "missing",
        "bigquery_queries": "loaded" if _is_loaded(knowledge.bigquery_queries) else "missing",
        "tenant_reference": "loaded" if _is_loaded(knowledge.tenant_reference) else "missing",
        "error_patterns": "loaded" if _is_loaded(knowledge.error_patterns) else "missing",
        "output_format": "loaded" if _is_loaded(knowledge.output_format) else "missing",
        "handoff_updates": "loaded" if _is_loaded(knowledge.handoff_updates) else "missing",
        "runbook_alloydb": "loaded" if _is_loaded(knowledge.runbook_alloydb) else "missing",
        "runbook_pubsub": "loaded" if _is_loaded(knowledge.runbook_pubsub) else "missing",
        "runbook_cloud_run": "loaded" if _is_loaded(knowledge.runbook_cloud_run) else "missing",
        "runbook_integrations": "loaded" if _is_loaded(knowledge.runbook_integrations) else "missing",
        "runbook_secops": "loaded" if _is_loaded(knowledge.runbook_secops) else "missing",
    }


def clear_knowledge_cache():
    """Clear the cached knowledge (useful for testing or reloading)."""
    load_sre_knowledge.cache_clear()
