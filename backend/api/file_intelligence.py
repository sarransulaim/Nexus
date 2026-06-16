"""
file_intelligence.py — File Analysis Engine
=============================================
Reads any uploaded file, extracts content, asks Claude what it contains
and what should be done with it. Returns structured JSON for confirmation.

Supported file types:
  PDF       → pymupdf text extraction
  DOCX      → python-docx text extraction
  XLSX/CSV  → pandas tabular analysis
  PNG/JPG   → Claude vision (multimodal)
  TXT/MD    → plain text
  Anything else → reject with friendly error

Output (always a dict):
  {
    "type":        "project" | "meeting_notes" | "data_table" | "image" | "general",
    "title":       "Short title for what this is",
    "summary":     "1-2 sentence summary in plain English",
    "confidence":  0-100,
    "proposed_actions": [...],   # what AI thinks should happen
    "raw_extract": "...",        # truncated text for context
    "needs_review": bool         # AI uncertain about something
  }
"""

import os
import io
import json
import logging
import base64
from typing import Optional
from pathlib import Path

log = logging.getLogger("nexus.file_intel")

# ── Size limits ───────────────────────────────────────────────
MAX_FILE_SIZE       = 50 * 1024 * 1024       # 50 MB hard cap
MAX_TEXT_FOR_CLAUDE = 150_000                # ~ 40K tokens — safe for Sonnet
MAX_EXTRACT_PREVIEW = 5000                   # what we return to frontend


# ═══════════════════════════════════════════════════════════════
# MIME DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_real_mime(file_path: str) -> str:
    """
    Detect actual MIME type from file bytes, not just extension.
    Prevents disguised .exe files etc.

    Falls back to extension-based detection if python-magic isn't installed.
    """
    try:
        import magic
        return magic.from_file(file_path, mime=True)
    except ImportError:
        # python-magic not installed — fall back to extension
        ext = Path(file_path).suffix.lower()
        ext_map = {
            ".pdf":  "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc":  "application/msword",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls":  "application/vnd.ms-excel",
            ".csv":  "text/csv",
            ".txt":  "text/plain",
            ".md":   "text/markdown",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif":  "image/gif",
            ".webp": "image/webp",
        }
        return ext_map.get(ext, "application/octet-stream")
    except Exception as e:
        log.warning(f"MIME detection error: {e}")
        return "application/octet-stream"


def is_supported(mime_type: str) -> bool:
    return mime_type in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "text/csv",
        "text/plain",
        "text/markdown",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }


# ═══════════════════════════════════════════════════════════════
# TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════════

def extract_text(file_path: str, mime_type: str) -> str:
    """
    Extract plain text from any supported file.
    Returns empty string for images (use vision path instead).
    """
    try:
        # ── PDF ─────────────────────────────────────────────
        if mime_type == "application/pdf":
            try:
                import fitz   # pymupdf
                doc = fitz.open(file_path)
                pages = []
                for page in doc:
                    pages.append(page.get_text())
                doc.close()
                return "\n\n".join(pages)
            except Exception as e:
                log.error(f"PDF extraction error: {e}")
                return ""

        # ── DOCX ────────────────────────────────────────────
        if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            try:
                from docx import Document
                doc = Document(file_path)
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception as e:
                log.error(f"DOCX extraction error: {e}")
                return ""

        # ── Excel/CSV ───────────────────────────────────────
        if mime_type in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
        ):
            try:
                import pandas as pd
                if mime_type == "text/csv":
                    df = pd.read_csv(file_path)
                else:
                    df = pd.read_excel(file_path)

                # Summarize the data, don't dump every row
                summary = []
                summary.append(f"Spreadsheet with {len(df)} rows and {len(df.columns)} columns")
                summary.append(f"Columns: {', '.join(str(c) for c in df.columns)}")
                summary.append("\nFirst 20 rows:")
                summary.append(df.head(20).to_string())
                if len(df) > 20:
                    summary.append(f"\n... and {len(df) - 20} more rows")
                return "\n".join(summary)
            except Exception as e:
                log.error(f"Spreadsheet extraction error: {e}")
                return ""

        # ── Plain text / markdown ───────────────────────────
        if mime_type in ("text/plain", "text/markdown"):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception as e:
                log.error(f"Text read error: {e}")
                return ""

        # ── Images return empty — handled separately by vision path
        if mime_type.startswith("image/"):
            return ""

        return ""

    except Exception as e:
        log.error(f"extract_text unexpected error: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════
# AI ANALYSIS
# ═══════════════════════════════════════════════════════════════

ANALYSIS_SYSTEM_PROMPT = """You are a document analyzer for a project management system.

A user uploaded a file. Your job:
1. Figure out what KIND of document it is
2. Extract structured information about what should be done with it
3. Return ONLY a valid JSON object — no prose, no markdown fences, just JSON

Output format (use these exact keys):

{
  "type":       "project" | "meeting_notes" | "data_table" | "image" | "general",
  "title":      "Short descriptive title (under 80 chars)",
  "summary":    "1-2 sentence plain-English summary",
  "confidence": 0-100,
  "needs_review": true | false,
  "proposed_actions": [
    {
      "action": "create_project" | "create_task" | "add_employee" | "schedule_meeting" | "none",
      "details": { ... action-specific fields ... }
    }
  ]
}

TYPE GUIDE:
- "project": looks like a project plan, spec, requirements doc. Multiple tasks/deliverables.
- "meeting_notes": minutes, agenda, action items from a meeting
- "data_table": spreadsheet of employees, tasks, metrics, anything tabular
- "image": photo, screenshot, diagram, chart
- "general": doesn't fit other categories — provide summary only

ACTION-SPECIFIC FIELDS:

For create_project:
  {"name": "...", "description": "...", "priority": "Low|Medium|High|Critical",
   "due_date": "YYYY-MM-DD or null", "tasks": [
       {"title": "...", "description": "...", "priority": "...",
        "due_date": "YYYY-MM-DD or null", "skill_hint": "skills needed",
        "suggested_owner_id": null,
        "suggested_owner_reason": "why this person fits, or null if unsure"}
   ]}

For create_task (standalone, not part of project):
  {"title": "...", "description": "...", "priority": "...",
   "due_date": "YYYY-MM-DD or null", "skill_hint": "...",
   "suggested_owner_id": null,
   "suggested_owner_reason": "why this person fits, or null if unsure"}

For schedule_meeting:
  {"topic": "...", "scheduled_time": "...", "duration_minutes": N,
   "attendee_names": ["..."], "location": "..."}

For add_employee:
  {"name": "...", "role": "...", "team": "...", "skills": "...",
   "email": "email address if present in the file, else null",
   "experience": "years of experience as a NUMBER if present (e.g. 8), else null"}

IMPORTANT:
- If the file is unclear or could be multiple things, set needs_review: true
- For tasks/projects WITHOUT clear deadline, set due_date: null
- For skill_hint, write actual skills needed (e.g., "React, frontend") not job titles
- For suggested_owner_id, pick the BEST employee ID from the provided list whose skills
  match the task. Set to null if no employee is a good match.
- suggested_owner_reason should be one short sentence — e.g. "React skills match task"
- The manager will see your suggestions and decide. Don't assume anything is auto-applied.
- Output ONLY the JSON. No code fences. No prose before or after."""


def analyze_text_content(text: str, filename: str, employee_context: list = None) -> dict:
    """
    Send extracted text to Claude Sonnet for structured analysis.
    Returns the parsed JSON dict.
    """
    from api.ai_router import ai_router, TaskType
    import anthropic

    # Truncate if too large
    if len(text) > MAX_TEXT_FOR_CLAUDE:
        text = text[:MAX_TEXT_FOR_CLAUDE] + f"\n\n[... truncated, original was {len(text)} chars ...]"

    # Add employee context so Claude can match skills
    emp_block = ""
    if employee_context:
        emp_lines = ["Available employees and their skills:"]
        for emp in employee_context[:30]:
            emp_lines.append(
                f"- ID:{emp['id']} | {emp['name']} | {emp.get('role', '')} | "
                f"Skills: {emp.get('skills', 'none listed')}"
            )
        emp_block = "\n".join(emp_lines) + "\n\n"

    user_prompt = (
        f"Filename: {filename}\n\n"
        f"{emp_block}"
        f"File content:\n---\n{text}\n---\n\n"
        f"Analyze this file and return the JSON."
    )

    try:
        response = ai_router.call(
            task_type=TaskType.ORCHESTRATOR,    # use Sonnet for quality
            prompt=user_prompt,
            system=ANALYSIS_SYSTEM_PROMPT,
            max_tokens=4096,
        )
        return _parse_json_response(response)
    except Exception as e:
        log.error(f"AI analysis failed: {e}")
        return {
            "type": "general",
            "title": filename,
            "summary": f"Could not analyze file: {str(e)}",
            "confidence": 0,
            "needs_review": True,
            "proposed_actions": [],
        }


def analyze_image(file_path: str, filename: str, employee_context: list = None) -> dict:
    """
    Send image directly to Claude Sonnet's vision capability.
    Returns the parsed JSON dict.
    """
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

    # Read image as base64
    with open(file_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    # Determine media type
    ext = Path(file_path).suffix.lower().lstrip(".")
    media_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                 "gif": "image/gif", "webp": "image/webp"}
    media_type = media_map.get(ext, "image/jpeg")

    emp_block = ""
    if employee_context:
        emp_lines = ["Available employees:"]
        for emp in employee_context[:30]:
            emp_lines.append(f"- ID:{emp['id']} | {emp['name']} | {emp.get('role', '')}")
        emp_block = "\n".join(emp_lines) + "\n\n"

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=ANALYSIS_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Filename: {filename}\n\n{emp_block}Analyze this image and return the JSON.",
                    },
                ],
            }],
        )

        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        full_text  = "".join(text_parts)
        return _parse_json_response(full_text)

    except Exception as e:
        log.error(f"Vision analysis failed: {e}")
        return {
            "type": "image",
            "title": filename,
            "summary": f"Could not analyze image: {str(e)}",
            "confidence": 0,
            "needs_review": True,
            "proposed_actions": [],
        }


# ═══════════════════════════════════════════════════════════════
# JSON PARSER
# ═══════════════════════════════════════════════════════════════

def _parse_json_response(text: str) -> dict:
    """
    Claude usually returns clean JSON but sometimes wraps in fences.
    Extract the JSON object safely.
    """
    text = (text or "").strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop first line (```json or ```) and last line if it's ```
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    # Find the JSON object
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        log.warning(f"No JSON object found in response: {text[:200]}")
        return _fallback_response()

    json_str = text[start:end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log.warning(f"JSON parse error: {e} — text was: {json_str[:300]}")
        return _fallback_response()


def _fallback_response() -> dict:
    return {
        "type": "general",
        "title": "Unrecognized content",
        "summary": "The AI could not produce a structured analysis. Please review manually.",
        "confidence": 0,
        "needs_review": True,
        "proposed_actions": [],
    }


# ═══════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def analyze_file(file_path: str, original_filename: str, employee_context: list = None) -> dict:
    """
    Main entry point. Detects type, extracts content, runs AI analysis.
    Returns the structured dict for frontend confirmation UI.
    """
    if not os.path.exists(file_path):
        return _fallback_response() | {"summary": "File not found on server."}

    if os.path.getsize(file_path) > MAX_FILE_SIZE:
        return _fallback_response() | {
            "summary": f"File exceeds 50MB limit. Upload a smaller file or extract the key sections first.",
        }

    mime = detect_real_mime(file_path)

    if not is_supported(mime):
        return {
            "type": "general",
            "title": original_filename,
            "summary": f"Unsupported file type: {mime}. Supported: PDF, DOCX, XLSX, CSV, TXT, MD, PNG, JPG.",
            "confidence": 0,
            "needs_review": True,
            "proposed_actions": [],
        }

    # Image path — use vision
    if mime.startswith("image/"):
        result = analyze_image(file_path, original_filename, employee_context)
        result["raw_extract"] = "[image — see preview]"
        return result

    # Text path — extract first, then analyze
    text = extract_text(file_path, mime)
    if not text.strip():
        return {
            "type": "general",
            "title": original_filename,
            "summary": "Could not extract any text from this file. It may be scanned, encrypted, or empty.",
            "confidence": 0,
            "needs_review": True,
            "proposed_actions": [],
        }

    result = analyze_text_content(text, original_filename, employee_context)
    result["raw_extract"] = text[:MAX_EXTRACT_PREVIEW]
    return result