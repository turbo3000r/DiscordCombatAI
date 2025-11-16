import asyncio
import threading
from typing import Optional
from google import genai
from google.genai import types
from modules.LoggerHandler import get_logger

logger = get_logger()
_client_locks = {}
_client_locks_lock = threading.Lock()


def _get_lock_for_api_key(api_key: str) -> threading.Lock:
    with _client_locks_lock:
        lock = _client_locks.get(api_key)
        if lock is None:
            lock = threading.Lock()
            _client_locks[api_key] = lock
        return lock


class AIHandler:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key or ""
        self.model = model
        # Only create client if API key is provided
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None

    @classmethod
    def from_guild(cls, guild) -> "AIHandler":
        api_key = getattr(guild, "api_key", "") or ""
        model = getattr(guild, "model", "gemini-1.5-flash") or "gemini-1.5-flash"
        return cls(api_key=api_key, model=model)
        
    @property
    def is_api_key_valid(self) -> bool:
        """
        Validates the API key by making a minimal test call.
        Returns: is_valid: bool
        """
        if not self.api_key or not self.client:
            return False
        
        try:
            # Make a minimal test call - just asking for "test" response
            # This is very lightweight and fast
            test_contents = [
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="test")],
                ),
            ]
            
            lock = _get_lock_for_api_key(self.api_key)
            with lock:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=test_contents,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=1,  # Minimal response to save tokens
                    ),
                )
            
            # If we got here without exception, the key is valid
            return True
            
        except Exception as e:
            return False
    
    def _generate_response_sync(self, prompt: str, *, system_instruction: Optional[str] = None, temperature: Optional[float] = 0.7) -> str:
        """Synchronous version of generate_response - runs in thread pool."""
        if not self.api_key or not self.client:
            raise RuntimeError("AI API key is not configured for this guild.")

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt),
                ],
            ),
        ]

        generate_content_config = types.GenerateContentConfig(
            temperature=temperature,
        )

        if system_instruction:
            generate_content_config.system_instruction = [
                types.Part.from_text(text=system_instruction),
            ]

        # Use a per-api-key lock to avoid client-level race conditions while
        # still allowing different api keys (i.e., agents) to run concurrently.
        lock = _get_lock_for_api_key(self.api_key)
        with lock:
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=generate_content_config,
                )
            except Exception as e:
                raise RuntimeError(f"AI generation failed: {e}")

        # Extract text safely
        try:
            return response.text or ""
        except Exception:
            return ""
    
    async def generate_response(self, prompt: str, *, system_instruction: Optional[str] = None, temperature: Optional[float] = 0.7) -> str:
        """
        Async version of generate_response that runs the blocking call in a thread pool.
        This prevents blocking the Discord event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._generate_response_sync(prompt, system_instruction=system_instruction, temperature=temperature)
        )

    
#AIHandler = AIHandler(api_key="231243434324", model="gemini-1.5-flash")