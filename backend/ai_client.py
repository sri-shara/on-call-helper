"""
AI Client Factory - creates Anthropic or Vertex AI client based on config.

The Anthropic SDK provides both clients with identical interfaces:
- Anthropic: Direct API access using ANTHROPIC_API_KEY
- AnthropicVertex: Vertex AI access using GCP credentials

Both use the same messages.create() API, same exceptions, same response format.
"""

import logging
from typing import Optional, Union

from anthropic import Anthropic, AnthropicVertex

from backend.config import settings

logger = logging.getLogger(__name__)

# Type alias for either client type
AIClient = Union[Anthropic, AnthropicVertex]


def create_ai_client(api_key: Optional[str] = None) -> Optional[AIClient]:
    """
    Create AI client based on USE_VERTEX setting.

    Args:
        api_key: Optional Anthropic API key override (ignored for Vertex)

    Returns:
        Anthropic or AnthropicVertex client, or None if credentials missing
    """
    if settings.use_vertex:
        if not settings.vertex_project_id:
            logger.warning("USE_VERTEX=true but VERTEX_PROJECT_ID not set")
            return None
        logger.info(
            f"Creating Vertex AI client "
            f"(project={settings.vertex_project_id}, region={settings.vertex_region})"
        )
        return AnthropicVertex(
            project_id=settings.vertex_project_id,
            region=settings.vertex_region,
        )
    else:
        key = api_key or settings.anthropic_api_key
        if not key:
            logger.warning("ANTHROPIC_API_KEY not set")
            return None
        logger.info("Creating Anthropic API client")
        return Anthropic(api_key=key)


def get_triage_model() -> str:
    """
    Get triage model name for current backend.

    Returns Vertex model name (e.g., claude-sonnet-4-5@20250929) or
    Anthropic model name (e.g., claude-sonnet-4-20250514).
    """
    if settings.use_vertex:
        return settings.vertex_triage_model
    return settings.triage_model


def get_fixer_model() -> str:
    """
    Get fixer model name for current backend.

    Returns Vertex model name or Anthropic model name.
    """
    if settings.use_vertex:
        return settings.vertex_fixer_model
    return settings.fixer_model


def get_backend_name() -> str:
    """
    Get human-readable backend name for error messages.

    Returns something like "Vertex AI (anthropic-vertex/us-east5)" or "Anthropic API".
    """
    if settings.use_vertex:
        return f"Vertex AI ({settings.vertex_project_id}/{settings.vertex_region})"
    return "Anthropic API"
