import json
from pathlib import Path
import anthropic

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "extract_call.txt"
_COACHING_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "coaching_extract.txt"


def extract_call_data(transcript: str) -> dict:
    """Sends a transcript to Claude and returns structured data as a dict."""
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    message = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": transcript}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw)


def extract_from_coaching(transcript: str, analysis: str) -> dict:
    """Structures a coach's written analysis into the knowledge base JSON format."""
    system_prompt = _COACHING_PROMPT_PATH.read_text(encoding="utf-8")
    user_content = f"{analysis}\n\n---\n\nTRASCRIZIONE:\n{transcript}"

    message = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw)
