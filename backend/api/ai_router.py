"""
ai_router.py — Hybrid Multi-Model AI Router
=============================================
The cost-optimal brain. Every AI call in Nexus goes through here.

Routes by task type to the cheapest model that can handle it.
Falls back gracefully when local models are down.

Models used:
  Claude Sonnet 4.5    — orchestrator brain, negotiation (premium)
  Claude Haiku 4.5     — simple commands, reads, drafts (cheap)
  Gemini 2.5 Pro       — all Google Workspace (best for Gmail/Calendar)
  Ollama qwen2.5:7b    — local, free: summaries, briefings, extraction

Fallback chain:
  Ollama unavailable → Haiku (paid but reliable)
  Gemini unavailable → Haiku
  All fail → Sonnet (last resort, expensive but always works)
"""

import os
import logging
from typing import Optional
from enum import Enum

log = logging.getLogger("nexus.ai_router")


class TaskType(str, Enum):
    """Every AI call must declare its task type for proper routing."""
    ORCHESTRATOR          = "orchestrator"            # tool calling, manager/employee commands
    NEGOTIATION           = "negotiation"             # multi-agent reasoning
    SIMPLE_COMMAND        = "simple_command"          # quick reads, status checks
    EMAIL_SUMMARY         = "email_summary"           # Gmail read + summarize
    EMAIL_DRAFT           = "email_draft"             # compose reply in user's voice
    CALENDAR_ANALYSIS     = "calendar_analysis"       # focus time, schedule patterns
    DAILY_BRIEFING        = "daily_briefing"          # morning briefing
    CHAT_SUMMARY          = "chat_summary"            # summarize unread channel
    ACTION_ITEM_DETECTION = "action_item_detection"   # find tasks in chat/meetings
    PREFERENCE_EXTRACTION = "preference_extraction"   # learn user patterns
    MEETING_SUMMARY       = "meeting_summary"         # post-meeting summary
    GENERAL_DRAFT         = "general_draft"           # any other AI text


# ═══════════════════════════════════════════════════════════════
# MODEL MAPPING
# ═══════════════════════════════════════════════════════════════

ROUTING_TABLE = {
    TaskType.ORCHESTRATOR:          ("claude", "claude-sonnet-4-5"),
    TaskType.NEGOTIATION:           ("claude", "claude-sonnet-4-5"),
    TaskType.SIMPLE_COMMAND:        ("claude", "claude-haiku-4-5"),
    TaskType.EMAIL_SUMMARY:         ("gemini", "gemini-2.5-pro"),
    TaskType.EMAIL_DRAFT:           ("gemini", "gemini-2.5-pro"),
    TaskType.CALENDAR_ANALYSIS:     ("gemini", "gemini-2.5-pro"),
    TaskType.DAILY_BRIEFING:        ("ollama", "qwen2.5:7b"),
    TaskType.CHAT_SUMMARY:          ("ollama", "qwen2.5:7b"),
    TaskType.ACTION_ITEM_DETECTION: ("ollama", "qwen2.5:7b"),
    TaskType.PREFERENCE_EXTRACTION: ("ollama", "qwen2.5:7b"),
    TaskType.MEETING_SUMMARY:       ("ollama", "qwen2.5:7b"),
    TaskType.GENERAL_DRAFT:         ("claude", "claude-haiku-4-5"),
}

# Fallback chain when primary fails
FALLBACKS = {
    "ollama":  ("claude", "claude-haiku-4-5"),
    "gemini":  ("claude", "claude-haiku-4-5"),
    "claude":  None,   # no fallback for Claude itself
}


# ═══════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════

class AIRouter:
    """
    Central router for all AI calls. Holds lazily-initialized clients
    for each provider and dispatches based on task type.
    """

    def __init__(self):
        self._claude_client = None
        self._gemini_client = None
        self._ollama_client = None

    # ── Lazy client initialization ────────────────────────────

    @property
    def claude(self):
        if self._claude_client is None:
            import anthropic
            self._claude_client = anthropic.Anthropic(
                api_key=os.getenv("CLAUDE_API_KEY")
            )
        return self._claude_client

    @property
    def gemini(self):
        if self._gemini_client is None:
            try:
                import google.genai as genai
                from google.genai import types as _gtypes
                self._gemini_client = genai.Client(
                    api_key=os.getenv("GEMINI_API_KEY"),
                    # 60s cap: a Gemini stall now RAISES, so AIRouter.call()'s except
                    # fires and falls back to Haiku instead of hanging the worker forever.
                    http_options=_gtypes.HttpOptions(timeout=60_000),
                )
            except Exception as e:
                log.warning(f"Gemini client init failed: {e}")
                return None
        return self._gemini_client

    @property
    def ollama(self):
        if self._ollama_client is None:
            from api.ollama_client import OllamaClient
            self._ollama_client = OllamaClient()
        return self._ollama_client

    # ── Main routing method ───────────────────────────────────

    def call(
        self,
        task_type: TaskType,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 2048,
        allow_fallback: bool = True,
    ) -> str:
        """
        Route a single-turn AI call. Returns text response.

        For multi-turn conversations with tool calls (the orchestrator),
        use claude_client directly — this method is for one-shot text gen.

        allow_fallback=False keeps a task pinned to its primary provider: used by
        the $0 background jobs (e.g. preference extraction) so that an Ollama
        outage silently SKIPS the work instead of quietly escalating a supposedly
        free task to paid Haiku on every run.
        """
        provider, model = ROUTING_TABLE.get(
            task_type,
            ("claude", "claude-haiku-4-5"),
        )

        try:
            return self._dispatch(provider, model, prompt, system, max_tokens)
        except Exception as e:
            log.warning(f"{provider} failed for {task_type.value}: {e}")
            fallback = FALLBACKS.get(provider) if allow_fallback else None
            if fallback:
                fb_provider, fb_model = fallback
                log.info(f"Falling back to {fb_provider}:{fb_model}")
                try:
                    return self._dispatch(fb_provider, fb_model, prompt, system, max_tokens)
                except Exception as e2:
                    log.error(f"Fallback also failed: {e2}")
                    return f"[AI router error: all providers failed for {task_type.value}]"
            return f"[AI router error: {provider} failed for {task_type.value}]"

    def _dispatch(self, provider, model, prompt, system, max_tokens):
        if provider == "claude":
            return self._call_claude(model, prompt, system, max_tokens)
        elif provider == "gemini":
            return self._call_gemini(model, prompt, system)
        elif provider == "ollama":
            return self._call_ollama(model, prompt, system)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    # ── Provider-specific calls ───────────────────────────────

    def _call_claude(self, model: str, prompt: str, system: Optional[str], max_tokens: int) -> str:
        kwargs = {
            "model":      model,
            "max_tokens": max_tokens,
            "messages":   [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        response = self.claude.messages.create(**kwargs)
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""

    def _call_gemini(self, model: str, prompt: str, system: Optional[str]) -> str:
        if not self.gemini:
            raise RuntimeError("Gemini client not available (no GEMINI_API_KEY?)")

        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"

        response = self.gemini.models.generate_content(
            model=model,
            contents=full_prompt,
        )
        return response.text or ""

    def _call_ollama(self, model: str, prompt: str, system: Optional[str]) -> str:
        return self.ollama.generate(model=model, prompt=prompt, system=system)


# ── Singleton — import this everywhere ────────────────────────
ai_router = AIRouter()


# ── Convenience function ──────────────────────────────────────
def ai_call(task_type: TaskType, prompt: str, system: Optional[str] = None,
            max_tokens: int = 2048, allow_fallback: bool = True) -> str:
    """Shortcut: from api.ai_router import ai_call, TaskType"""
    return ai_router.call(task_type, prompt, system, max_tokens, allow_fallback=allow_fallback)