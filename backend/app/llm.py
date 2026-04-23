from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

logger = logging.getLogger(__name__)


SUMMARY_INSTRUCTIONS = """You produce meeting summaries for an internal task tracker. Your output is inserted directly into a ClickUp task description, so it must be clean markdown with no preamble, no code fences, and no trailing commentary. Mirror the structure of a professional meeting-notes tool (Fathom-style): Meeting Purpose, Key Takeaways, Topics, Next Steps.

# Grounding rules
- Use ONLY information explicitly stated in <transcript>. Do not infer intent, speculate on motives, or add outside knowledge.
- Never put words in a person's mouth. If a point was raised but not attributed, describe it without naming a speaker.
- Use attendees' real names from the context block when attribution is clear. If a speaker is "Unknown" or the attribution is ambiguous, refer to them as "a participant".
- Write in past tense, third person, neutral factual tone. No filler ("great discussion", "productive meeting"), no hedging, no emojis, no opinions.
- Skip pure pleasantries, scheduling chatter, and off-topic banter.
- Plain text only. Do NOT wrap any phrases in markdown links. No URLs.
- If <transcript> is empty, off-topic, or has fewer than ~3 substantive exchanges, output EXACTLY this line and nothing else:
  `Summary unavailable — meeting was too brief or lacked substantive content.`

# Output format
Markdown only. Use these H2 sections, in this exact order. OMIT any section that has no real content — do not write "None", "N/A", or an empty bulleted list.

## Meeting Purpose
One sentence stating why the meeting happened and its primary objective.

## Key Takeaways
Bulleted list of 3–6 most important conclusions, decisions, or facts. Start each bullet with a bold short label followed by a colon, then the detail. Example: `- **Project Goal:** The team will migrate the marketing site to Webflow by end of Q2.`

## Topics
For each substantive topic discussed:
- Put the topic name in bold on its own line (e.g. `**Technology Decision**`)
- Under it, a short bulleted list of the specific facts, positions, or conclusions for that topic
- Use a blank line between topics
- No nested bullets beyond one level

## Next Steps
Bulleted list of concrete follow-ups, in the form `[Owner Name]: [action] [— by <deadline>]`. Omit the deadline clause if none was stated. Omit this whole section if there are no real follow-ups.

# Meeting context
Title: {title}
Date: {date}
Attendees: {attendees}
"""


TITLE_INSTRUCTIONS = """You generate a short, informative title for a meeting, based on the meeting summary or transcript excerpt. The title is shown in a task tracker, so it must be concrete and scannable.

# Rules
- 4–9 words, 60 characters max.
- Title Case.
- Name the actual topic, project, person, or decision. Examples: "ClickUp Brain AI Project Onboarding", "Q2 Marketing Site Rebuild Kickoff", "Harish Onboarding: Agentic Infrastructure Role", "Webflow vs Next.js Architecture Decision".
- Do NOT start with "Meeting about", "Discussion on", "Call with", or similar filler.
- No quotes, no emojis, no trailing punctuation, no prefix like "Title:".
- If the content is too generic or empty to name specifically, return exactly: Team Sync

# Output
Return ONLY the title string, nothing else."""


ACTION_ITEMS_INSTRUCTIONS = """You extract explicit action items from meeting transcripts. Each item becomes a subtask in a task tracker, so PRECISION outweighs RECALL — a missed item is acceptable; a hallucinated one is not.

# What qualifies as an action item
Both conditions must hold:
  1. Concrete deliverable — a specific thing to do or produce.
  2. Identifiable owner — a person who accepted the task, or was assigned and did not object.

# What DOES NOT qualify (skip these)
- Hypotheticals: "we could maybe…", "it might be worth…", "someone should eventually…"
- Suggestions nobody accepted: "you should probably X" with no agreement
- Questions or discussion points with no commitment attached
- Agreements in principle with no concrete next step ("we should stay aligned", "sounds good")
- Ongoing responsibilities already part of someone's role
- Things the group explicitly decided NOT to do
- Duplicate items — if the same task surfaces multiple times, emit it once using the most specific phrasing

# Assignee attribution
- `assignee_name` MUST exactly match a name from the Attendees list when possible (copy the spelling from that list).
- Credit the person who ACCEPTS the task, not the person who proposes it — unless they are the same person.
- If ownership is genuinely ambiguous, set `assignee_name` to null. Do NOT guess.
- Never invent a person. If the transcript names someone not in the Attendees list, you may still use that name verbatim, but prefer null over guessing.

# Deadline resolution
- Populate `deadline` ONLY if the transcript explicitly states a due date or timeframe.
- Resolve relative dates to ISO `YYYY-MM-DD` using the meeting date:
  - "by Friday" → the first Friday on or after the meeting date
  - "end of week" → the Friday of the meeting's week (Mon–Fri)
  - "next Tuesday" → the Tuesday of the week after the meeting's week
  - "end of month" → last day of the meeting's month
  - "tomorrow" → the day after the meeting date
- If a date is given with no year and is ambiguous, use the meeting's year.
- For vague horizons ("soon", "eventually", "when you get to it", "ASAP"), set `deadline` to null.

# Description
- Start with an imperative verb: "Send…", "Schedule…", "Review…", "Draft…", "Follow up with…".
- Specific enough that the owner could act without re-reading the transcript (include the object: which deck, which customer, which doc).
- Do NOT include the owner's name (that's in `assignee_name`).
- Do NOT include the deadline (that's in `deadline`).
- Keep under 140 characters when you can.

# Few-shot example
Meeting date: 2026-04-22 (a Wednesday).
Attendees: Alice, Bob, Carol.

Transcript excerpt:
  Alice: The pricing deck needs updating before the Acme demo next Thursday.
  Bob: I'll take that — draft ready by Tuesday for review.
  Alice: Great. And someone should maybe look at the competitive landscape at some point.
  Carol: I can probably do that after the demo.
  Bob: Also, I need to send Acme the updated security questionnaire response by end of week.

Correct output:
{{
  "action_items": [
    {{"description": "Draft updated pricing deck for Acme demo", "assignee_name": "Bob", "deadline": "2026-04-28"}},
    {{"description": "Send Acme the updated security questionnaire response", "assignee_name": "Bob", "deadline": "2026-04-24"}}
  ]
}}

Why Carol's and Alice's items were skipped:
- Alice's "someone should maybe look at" is a hypothetical, no owner, no commitment.
- Carol's "I can probably do that after the demo" has no specific deliverable definition, no concrete deadline, and the hedge "probably" means no firm commitment.

# Output
Return a JSON object with a single key `action_items`: an array (empty if none qualify). The schema is enforced; do not include extra keys and do not wrap the response in prose or code fences.

# Meeting context
Date: {date}
Attendees: {attendees}
"""


ACTION_ITEMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "description": {"type": "string"},
                    "assignee_name": {"type": ["string", "null"]},
                    "deadline": {"type": ["string", "null"]},
                },
                "required": ["description", "assignee_name", "deadline"],
            },
        },
    },
    "required": ["action_items"],
}


def transcript_to_text(transcript: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in transcript or []:
        speaker = (row.get("speaker") or {}).get("display_name") or "Unknown"
        text = row.get("text") or ""
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _format_attendees(attendees: list[dict[str, Any]] | None) -> str:
    if not attendees:
        return "(not provided)"
    names = [a.get("name") for a in attendees if a.get("name")]
    return ", ".join(names) if names else "(not provided)"


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_output_text(data: dict[str, Any]) -> str:
    # The top-level "output_text" is an SDK-only convenience; walk the raw Responses API structure.
    text = data.get("output_text")
    if isinstance(text, str) and text:
        return text.strip()
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                t = content.get("text")
                if isinstance(t, str) and t:
                    return t.strip()
    return ""


async def _post_responses(payload: dict[str, Any], retries: int = 3) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for LLM fallback")

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(OPENAI_RESPONSES_URL, headers=headers, json=payload)
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt < retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                response.raise_for_status()
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt < retries - 1:
                logger.warning("OpenAI request failed (attempt %s/%s): %s", attempt + 1, retries, exc)
                await asyncio.sleep(2**attempt)
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError("Unknown OpenAI request error")


async def _run_text(instructions: str, user_input: str) -> str:
    settings = get_settings()
    data = await _post_responses(
        {
            "model": settings.openai_model,
            "instructions": instructions,
            "input": user_input,
        }
    )
    return _extract_output_text(data)


async def _run_structured(
    instructions: str,
    user_input: str,
    schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any] | None:
    settings = get_settings()
    data = await _post_responses(
        {
            "model": settings.openai_model,
            "instructions": instructions,
            "input": user_input,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                },
            },
        }
    )
    return _parse_json_object(_extract_output_text(data))


async def generate_meeting_title(context: str) -> str:
    if not context or not context.strip():
        return ""
    try:
        raw = await _run_text(TITLE_INSTRUCTIONS, f"<content>\n{context.strip()[:6000]}\n</content>")
    except Exception:
        return ""
    cleaned = raw.strip().strip('"').strip("'").rstrip(".")
    if cleaned.lower().startswith("title:"):
        cleaned = cleaned[len("title:") :].strip()
    return cleaned[:80]


async def summarize_meeting(
    transcript: list[dict[str, Any]],
    *,
    meeting_title: str | None = None,
    meeting_date: str | None = None,
    attendees: list[dict[str, Any]] | None = None,
) -> str:
    content = transcript_to_text(transcript)
    if not content:
        return "Summary unavailable — empty transcript."

    instructions = SUMMARY_INSTRUCTIONS.format(
        title=meeting_title or "(not provided)",
        date=meeting_date or "(not provided)",
        attendees=_format_attendees(attendees),
    )
    user_input = f"<transcript>\n{content}\n</transcript>"
    return await _run_text(instructions, user_input)


async def extract_action_items(
    transcript: list[dict[str, Any]],
    attendees: list[dict[str, Any]],
    *,
    meeting_date: str | None = None,
) -> list[dict[str, Any]]:
    content = transcript_to_text(transcript)
    if not content:
        return []

    instructions = ACTION_ITEMS_INSTRUCTIONS.format(
        date=meeting_date or "(not provided)",
        attendees=_format_attendees(attendees),
    )
    user_input = f"<transcript>\n{content}\n</transcript>"

    parsed = await _run_structured(instructions, user_input, ACTION_ITEMS_SCHEMA, "action_items")
    if not parsed:
        return []

    items = parsed.get("action_items")
    if not isinstance(items, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        description = (item.get("description") or "").strip()
        if not description:
            continue
        cleaned.append(
            {
                "description": description,
                "assignee_name": item.get("assignee_name"),
                "deadline": item.get("deadline"),
            }
        )
    return cleaned
