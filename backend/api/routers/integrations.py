"""
integrations.py — reports which external integrations THIS instance has configured.
Returns booleans only (presence of the relevant env vars) — never the secrets
themselves — so the Integrations page can show honest, real status.
"""
import os
from fastapi import APIRouter, Depends

from database.models import Employee
from api.security import get_current_user

router = APIRouter()


def _configured(*env_names: str) -> bool:
    return any(os.getenv(n) for n in env_names)


@router.get("/status")
def integrations_status(current_user: Employee = Depends(get_current_user)):
    """Which of the integrations the backend actually supports are configured here."""
    return {
        "slack":    _configured("SLACK_BOT_TOKEN"),
        "whatsapp": _configured("TWILIO_ACCOUNT_SID"),
        "telegram": _configured("TELEGRAM_BOT_TOKEN"),
        "google":   _configured("GOOGLE_CLIENT_ID"),
    }
