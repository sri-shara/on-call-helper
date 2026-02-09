"""
Google Chat message poller for On Call Helper.

Periodically polls a Google Chat space for new messages using the
Chat API REST endpoint. New messages are parsed into incidents via
the gchat module and processed through the triage pipeline.

Follows the same polling pattern as GCPLoggingService.
"""

import asyncio
import logging
import subprocess
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

CHAT_API_BASE = "https://chat.googleapis.com/v1"


class GoogleChatPoller:
    """
    Polls a Google Chat space for new messages.

    Uses the Chat API to list messages newer than the last poll time,
    deduplicates by message ID, and invokes a callback for each new message.

    Auth: Uses `gcloud auth print-access-token` by default (user credentials),
    which avoids scope issues with ADC. Falls back to service account if
    credentials_path is provided.
    """

    def __init__(self, space_id: str, credentials_path: Optional[str] = None):
        self.space_id = space_id
        self._credentials_path = credentials_path
        self._polling_active = False
        self._polling_task: Optional[asyncio.Task] = None
        self._seen_message_ids: set = set()
        self._last_poll_time: Optional[datetime] = None

    @property
    def is_polling(self) -> bool:
        return self._polling_active

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get Authorization header for Chat API requests.

        Priority:
        1. Service account credentials_path (if set)
        2. Application Default Credentials with chat scope
           (requires: gcloud auth application-default login --scopes=...chat.messages.readonly)
        3. Fallback: gcloud auth print-access-token via subprocess
        """
        CHAT_SCOPES = ["https://www.googleapis.com/auth/chat.messages.readonly"]

        if self._credentials_path:
            import google.auth.transport.requests
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(
                self._credentials_path, scopes=CHAT_SCOPES
            )
            request = google.auth.transport.requests.Request()
            creds.refresh(request)
            return {"Authorization": f"Bearer {creds.token}"}

        # Try ADC first (works after gcloud auth application-default login with chat scope)
        try:
            import google.auth
            import google.auth.transport.requests

            creds, _ = google.auth.default(scopes=CHAT_SCOPES)
            request = google.auth.transport.requests.Request()
            creds.refresh(request)
            return {"Authorization": f"Bearer {creds.token}"}
        except Exception as e:
            logger.debug(f"ADC auth failed, falling back to gcloud CLI: {e}")

        # Fallback: gcloud CLI token
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gcloud auth failed: {result.stderr.strip()}")

        token = result.stdout.strip()
        return {"Authorization": f"Bearer {token}"}

    async def poll_once(self, callback: Callable) -> int:
        """
        Poll for new messages since last poll time.

        Args:
            callback: Async function called with raw message dict for each new message.

        Returns:
            Number of new messages processed.
        """
        # Time window: first poll looks back 30 min, subsequent polls use last_poll_time - 10s overlap
        if self._last_poll_time:
            start_time = self._last_poll_time - timedelta(seconds=10)
        else:
            start_time = datetime.utcnow() - timedelta(minutes=30)

        # Build Chat API request
        filter_str = f'createTime > "{start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")}"'
        url = f"{CHAT_API_BASE}/{self.space_id}/messages"
        params = {
            "filter": filter_str,
            "pageSize": 100,
            "orderBy": "createTime asc",
        }

        loop = asyncio.get_event_loop()

        # Get auth headers in thread pool (may shell out to gcloud)
        try:
            headers = await loop.run_in_executor(None, self._get_auth_headers)
        except Exception as e:
            logger.error(f"Chat API auth failed: {e}")
            self._last_poll_time = datetime.utcnow()
            return 0

        processed = 0

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Handle pagination
                while True:
                    response = await client.get(url, params=params, headers=headers)

                    if response.status_code == 403:
                        logger.error(
                            "Chat API 403 Forbidden — check that the Chat API is enabled "
                            "and your account has access to the space"
                        )
                        break

                    if response.status_code == 404:
                        logger.error(
                            f"Chat API 404 — space {self.space_id} not found. "
                            "Check GCHAT_SPACE_ID in config."
                        )
                        break

                    if response.status_code != 200:
                        logger.error(f"Chat API error {response.status_code}: {response.text[:200]}")
                        break

                    data = response.json()
                    messages = data.get("messages", [])

                    for msg in messages:
                        msg_id = msg.get("name", "")

                        # Deduplication
                        if msg_id in self._seen_message_ids:
                            continue
                        self._seen_message_ids.add(msg_id)

                        # Skip messages from the bot itself (sender.type == "BOT")
                        sender = msg.get("sender", {})
                        if sender.get("type") == "BOT":
                            continue

                        # Skip empty messages
                        text = msg.get("argumentText") or msg.get("text", "")
                        if not text.strip():
                            continue

                        try:
                            await callback(msg)
                            processed += 1
                        except Exception as e:
                            logger.error(f"Error processing chat message {msg_id}: {e}")

                    # Pagination
                    next_token = data.get("nextPageToken")
                    if next_token:
                        params["pageToken"] = next_token
                    else:
                        break

        except httpx.ConnectError as e:
            logger.error(f"Chat API connection error: {e}")
        except Exception as e:
            logger.error(f"Chat poll error: {e}")

        # Bound the seen set
        if len(self._seen_message_ids) > 5000:
            self._seen_message_ids = set(list(self._seen_message_ids)[-2500:])

        self._last_poll_time = datetime.utcnow()
        return processed

    async def start_polling(
        self,
        callback: Callable,
        interval_seconds: int = 30,
    ):
        """Start polling the Chat space for new messages."""
        if self._polling_active:
            logger.warning("Chat polling already active")
            return

        self._polling_active = True
        logger.info(f"Starting Google Chat polling every {interval_seconds}s for {self.space_id}")

        async def poll_loop():
            while self._polling_active:
                try:
                    count = await self.poll_once(callback)
                    if count > 0:
                        logger.info(f"Processed {count} new chat messages")
                except Exception as e:
                    logger.error(f"Chat polling error: {e}")

                await asyncio.sleep(interval_seconds)

        self._polling_task = asyncio.create_task(poll_loop())

    async def stop_polling(self):
        """Stop polling."""
        self._polling_active = False
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        logger.info("Google Chat polling stopped")
