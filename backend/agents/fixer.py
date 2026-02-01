"""
Fixer Agent for On Call Helper.

Uses Claude AI to generate minimal code fixes based on triage analysis.
Reads actual source code from GitHub and produces targeted fixes.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError

from backend.config import settings
from backend.models import TriageResult, FixResult, TriageClassification
from backend.services.github import GitHubService, GitHubError

logger = logging.getLogger(__name__)


class FixerError(Exception):
    """Error during fix generation."""
    pass


FIXER_SYSTEM_PROMPT = """You are a code fix agent for Nucleus, a Go-based MDR (Managed Detection & Response) platform.

## Nucleus Codebase Context
- Language: Go 1.24+
- Architecture: Microservices in backend/services/ and backend/processors/
- Database: PostgreSQL with sqlc for type-safe queries
- Testing: Go standard testing + testify
- Style: Follow existing patterns in the codebase

## Your Task
Generate a MINIMAL fix for the identified bug.

## Rules
1. Make the smallest possible change to fix the issue
2. Do NOT refactor unrelated code
3. Do NOT add features or improvements beyond the fix
4. Match existing code style exactly
5. Include error handling if the bug was caused by missing error handling
6. The fix must compile and pass `go build` and `go vet`
7. Preserve all imports and package declarations
8. Only modify the specific function or code block with the bug

## Output Format
You must respond with a JSON object containing your fix:

```json
{
    "file_path": "backend/services/...",
    "original_code": "the exact buggy code section (copy from source)",
    "fixed_code": "the corrected code section",
    "explanation": "why this fix works and what was wrong",
    "diff_summary": "brief one-line description of changes"
}
```

Important:
- `original_code` must be an EXACT copy from the source file (will be used for string replacement)
- `fixed_code` must be a drop-in replacement that compiles
- Keep the scope minimal - only include the affected function or code block
"""


class FixerAgent:
    """
    Claude-powered code fix generator.

    Generates minimal code fixes based on triage analysis, reading
    actual source code from GitHub to ensure accurate fixes.
    """

    MAX_ITERATIONS = 3

    def __init__(
        self,
        github_service: Optional[GitHubService] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """
        Initialize the fixer agent.

        Args:
            github_service: GitHub service for reading source files
            api_key: Anthropic API key (defaults to settings)
            model: Model to use (defaults to settings.fixer_model)
        """
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.fixer_model
        self.client: Optional[Anthropic] = None
        self.github = github_service

        if self.api_key:
            self.client = Anthropic(api_key=self.api_key)

    def _format_triage(self, triage: TriageResult, source_code: str) -> str:
        """Format triage result and source code for Claude."""
        parts = [
            "## Bug Analysis",
            f"**Root Cause**: {triage.root_cause}",
            f"**File**: {triage.file_path}",
            f"**Function**: {triage.function_name or 'Unknown'}",
            f"**Confidence**: {triage.confidence:.0%}",
            "",
            "## Current Source Code",
            "```go",
            source_code,
            "```",
        ]

        if triage.code_snippet:
            parts.extend([
                "",
                "## Problematic Code Snippet",
                "```go",
                triage.code_snippet,
                "```",
            ])

        if triage.suggested_fix:
            parts.extend([
                "",
                f"## Suggested Fix Approach",
                triage.suggested_fix,
            ])

        parts.extend([
            "",
            "---",
            "Generate a minimal fix for this bug. Output as JSON.",
        ])

        return "\n".join(parts)

    def _format_retry_prompt(
        self,
        triage: TriageResult,
        source_code: str,
        previous_fix: FixResult,
        feedback: str,
    ) -> str:
        """Format prompt for retry with CodeRabbit feedback."""
        base_prompt = self._format_triage(triage, source_code)

        retry_section = f"""

## Previous Attempt (Iteration {previous_fix.iteration})

Your previous fix had issues that need to be addressed:

### Previous Fix
```go
{previous_fix.fixed_code}
```

### CodeRabbit Feedback
{feedback}

Please generate an improved fix that addresses these issues.
"""

        return base_prompt + retry_section

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from Claude's response."""
        # Try to find JSON in code blocks first
        json_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(json_pattern, text)

        if matches:
            for match in matches:
                try:
                    return json.loads(match.strip())
                except json.JSONDecodeError:
                    continue

        # Try to find raw JSON object
        brace_pattern = r"\{[\s\S]*\}"
        matches = re.findall(brace_pattern, text)

        if matches:
            for match in sorted(matches, key=len, reverse=True):
                try:
                    return json.loads(match)
                except json.JSONDecodeError:
                    continue

        raise FixerError(f"No valid JSON found in response: {text[:500]}")

    def _parse_response(
        self,
        response_text: str,
        incident_id: str,
        iteration: int,
    ) -> FixResult:
        """Parse Claude's response into a FixResult."""
        data = self._extract_json(response_text)

        # Validate required fields
        required_fields = ["file_path", "original_code", "fixed_code", "explanation"]
        for field in required_fields:
            if field not in data or not data[field]:
                raise FixerError(f"Missing required field in fix response: {field}")

        return FixResult(
            incident_id=incident_id,
            file_path=data["file_path"],
            original_code=data["original_code"],
            fixed_code=data["fixed_code"],
            explanation=data["explanation"],
            diff_summary=data.get("diff_summary", "Code fix applied"),
            iteration=iteration,
        )

    def _normalize_whitespace(self, code: str) -> str:
        """Normalize whitespace for comparison."""
        lines = code.strip().split('\n')
        # Strip trailing whitespace from each line, normalize to single spaces
        normalized = []
        for line in lines:
            # Replace tabs with spaces, strip trailing whitespace
            line = line.replace('\t', '    ').rstrip()
            normalized.append(line)
        return '\n'.join(normalized)

    def _find_matching_code(self, source_code: str, original_code: str) -> Optional[str]:
        """
        Find the actual matching code in source, handling whitespace differences.

        Returns the actual code from source that matches, or None if not found.
        """
        # First try exact match
        if original_code in source_code:
            return original_code

        # Normalize both and try to find
        normalized_original = self._normalize_whitespace(original_code)

        # Try to find by normalizing source lines
        source_lines = source_code.split('\n')
        original_lines = normalized_original.split('\n')
        num_original_lines = len(original_lines)

        # Slide through source looking for matching block
        for i in range(len(source_lines) - num_original_lines + 1):
            candidate_lines = source_lines[i:i + num_original_lines]
            normalized_candidate = self._normalize_whitespace('\n'.join(candidate_lines))

            if normalized_candidate == normalized_original:
                # Found it! Return the actual source lines
                return '\n'.join(candidate_lines)

        # Try fuzzy matching with difflib
        import difflib

        # Split source into chunks of similar size to original
        original_line_count = len(original_lines)
        best_match = None
        best_ratio = 0.7  # Minimum 70% similarity threshold

        for i in range(len(source_lines) - original_line_count + 1):
            candidate = '\n'.join(source_lines[i:i + original_line_count])
            ratio = difflib.SequenceMatcher(
                None,
                self._normalize_whitespace(candidate),
                normalized_original
            ).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate

        if best_match and best_ratio > 0.85:  # High confidence match
            logger.info(f"Found fuzzy match with {best_ratio:.0%} similarity")
            return best_match

        return None

    def _validate_fix(self, fix: FixResult, source_code: str) -> Optional[str]:
        """
        Validate that the fix can be applied to the source.

        Returns:
            The actual matching code from source (may differ in whitespace)

        Raises:
            FixerError: If the fix is invalid
        """
        # Find the matching code in source
        actual_match = self._find_matching_code(source_code, fix.original_code)

        if actual_match is None:
            raise FixerError(
                "original_code not found in source file. "
                "The fix cannot be applied."
            )

        # Check that fixed_code is different
        if self._normalize_whitespace(fix.original_code) == self._normalize_whitespace(fix.fixed_code):
            raise FixerError("fixed_code is identical to original_code")

        # Basic syntax check - ensure balanced braces
        open_braces = fix.fixed_code.count("{")
        close_braces = fix.fixed_code.count("}")
        if open_braces != close_braces:
            raise FixerError(
                f"Unbalanced braces in fixed_code: {open_braces} open, {close_braces} close"
            )

        return actual_match

    async def generate_fix(
        self,
        triage: TriageResult,
        coderabbit_feedback: Optional[str] = None,
        previous_fix: Optional[FixResult] = None,
    ) -> FixResult:
        """
        Generate a code fix based on triage analysis.

        Args:
            triage: Triage result with bug analysis
            coderabbit_feedback: Optional feedback from CodeRabbit review
            previous_fix: Optional previous fix attempt for retry

        Returns:
            FixResult with the generated fix

        Raises:
            FixerError: If fix generation fails
        """
        if not self.client:
            raise FixerError("Anthropic client not initialized. Check ANTHROPIC_API_KEY.")

        if triage.classification != TriageClassification.FIXABLE:
            raise FixerError(
                f"Cannot generate fix for non-fixable classification: {triage.classification}"
            )

        if not triage.file_path:
            raise FixerError("Triage result missing file_path - cannot generate fix")

        # Determine iteration number
        iteration = 1
        if previous_fix:
            iteration = min(previous_fix.iteration + 1, self.MAX_ITERATIONS)

        logger.info(
            f"Generating fix for {triage.incident_id} "
            f"(iteration {iteration}/{self.MAX_ITERATIONS})"
        )

        # Fetch source code from GitHub
        source_code = await self._fetch_source_code(triage.file_path)

        # Build prompt
        if coderabbit_feedback and previous_fix:
            user_prompt = self._format_retry_prompt(
                triage, source_code, previous_fix, coderabbit_feedback
            )
        else:
            user_prompt = self._format_triage(triage, source_code)

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=FIXER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response_text = message.content[0].text
            logger.debug(f"Claude response for fix: {response_text[:500]}")

            fix = self._parse_response(response_text, triage.incident_id, iteration)

            # Validate the fix and get the actual matching code from source
            actual_match = self._validate_fix(fix, source_code)

            # Update fix with the actual code from source (for accurate replacement)
            if actual_match and actual_match != fix.original_code:
                logger.info("Adjusted original_code to match actual source formatting")
                fix.original_code = actual_match

            logger.info(
                f"Generated fix for {triage.incident_id}: {fix.diff_summary}"
            )

            return fix

        except APIConnectionError as e:
            logger.error(f"API connection error generating fix: {e}")
            raise FixerError(f"Failed to connect to Anthropic API: {e}")
        except RateLimitError as e:
            logger.error(f"Rate limited generating fix: {e}")
            raise FixerError(f"Rate limited by Anthropic API: {e}")
        except APIError as e:
            logger.error(f"API error generating fix: {e}")
            raise FixerError(f"Anthropic API error: {e}")

    async def _fetch_source_code(self, file_path: str) -> str:
        """
        Fetch source code from local Nucleus repository.

        Args:
            file_path: Path to the file relative to repo root

        Returns:
            File content as string

        Raises:
            FixerError: If file cannot be read
        """
        # Read from local filesystem instead of GitHub API
        local_path = settings.nucleus_repo_path / file_path

        logger.debug(f"Reading source file from: {local_path}")

        if not local_path.exists():
            raise FixerError(f"File not found: {local_path}")

        if not local_path.is_file():
            raise FixerError(f"Path is not a file: {local_path}")

        try:
            content = local_path.read_text(encoding="utf-8")
            logger.debug(f"Successfully read {file_path} ({len(content)} chars)")
            return content
        except Exception as e:
            raise FixerError(f"Failed to read source code: {e}")

    def generate_fix_sync(
        self,
        triage: TriageResult,
        coderabbit_feedback: Optional[str] = None,
        previous_fix: Optional[FixResult] = None,
    ) -> FixResult:
        """Synchronous version of generate_fix."""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.generate_fix(triage, coderabbit_feedback, previous_fix)
        )


# Module-level convenience function
async def generate_fix(
    triage: TriageResult,
    github_service: Optional[GitHubService] = None,
) -> FixResult:
    """
    Generate a fix using the default fixer agent.

    Args:
        triage: Triage result with bug analysis
        github_service: Optional GitHub service

    Returns:
        FixResult with the generated fix
    """
    agent = FixerAgent(github_service=github_service)
    return await agent.generate_fix(triage)
