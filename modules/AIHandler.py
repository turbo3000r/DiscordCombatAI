import threading
from typing import Optional
import google.generativeai as genai


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
        self.api_key = api_key
        self.model = model
        genai.configure(api_key=api_key)

    @classmethod
    def from_guild(cls, guild) -> "AIHandler":
        api_key = guild.__dict__.get("api_key") or ""
        model = guild.__dict__.get("model") or "gemini-1.5-flash"
        return cls(api_key=api_key, model=model)
    
    def generate_response(self, prompt: str, *, system_instruction: Optional[str] = None) -> str:
        if not self.api_key:
            raise RuntimeError("AI API key is not configured for this guild.")

        model = genai.GenerativeModel(model_name=self.model, system_instruction=system_instruction)

        # Use a per-api-key lock to avoid client-level race conditions while
        # still allowing different api keys (i.e., agents) to run concurrently.
        lock = _get_lock_for_api_key(self.api_key)
        with lock:
            try:
                response = model.generate_content(prompt)
            except Exception as e:
                raise RuntimeError(f"AI generation failed: {e}")

        # Extract text safely
        try:
            return response.text or ""
        except Exception:
            return ""
