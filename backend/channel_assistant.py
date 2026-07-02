"""
channel_assistant.py — Nexus channel/team assistant
====================================================
When someone @mentions the Nexus bot in a SHARED Slack channel, this
builds a conversational reply that the whole channel can read.

SAFETY MODEL — why this is a separate path from the personal DM agent:
A channel is visible to everyone in it, so this assistant uses ONLY what
is already visible in that channel (its recent messages) plus general
knowledge. It has NO tools and NO access to anyone's private data —
emails, personal tasks, calendars, or 1:1 DM history. That makes a public
reply structurally incapable of leaking private information, no matter who
asks. (The personal agent, with all that private context, runs ONLY in
DMs — see slack_bot.handle_dm.)
"""

import logging
from api.claude_orchestrator import claude_client, MODEL_MAP

log = logging.getLogger("nexus.slack")

CHANNEL_SYSTEM = """You are Nexus, an AI assistant participating in a shared Slack channel called #{channel}.

CRITICAL — this is a PUBLIC channel. Everyone in it can read your replies:
- Use ONLY the channel conversation provided plus general knowledge.
- You have NO access to any individual's private data — personal emails, private tasks, calendars, or direct-message history — and no tools to fetch it. If someone asks for a specific person's private information, say you can't share personal details in a channel and suggest they DM you for their own info.
- You are talking with a TEAM, not one person. Address people by name when it helps.
- Be helpful, concise and friendly; match the channel's tone.
- Keep replies short for Slack — usually a few sentences. Use bullets only when they genuinely help."""


def build_channel_reply(channel_name: str, transcript: str, speaker: str, question: str) -> str:
    """
    Returns Nexus's reply for a channel @mention. Pure conversation grounded
    in the channel's own (already-public) recent messages — no tools, no
    private data. Falls back to a friendly error string on failure.
    """
    system = CHANNEL_SYSTEM.format(channel=channel_name or "team")
    user_content = (
        f"Recent messages in #{channel_name or 'this channel'} "
        f"(oldest first):\n{transcript or '(no earlier messages)'}\n\n"
        f"{speaker} just mentioned you and said:\n\"{question}\"\n\n"
        f"Write your reply to the channel."
    )
    try:
        resp = claude_client.messages.create(
            model=MODEL_MAP["sonnet"],
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "".join(parts).strip() or "I'm here — how can I help the team?"
    except Exception as e:
        log.error(f"channel reply failed: {e}")
        return "Sorry, I hit a snag answering that. Please try again in a moment."
