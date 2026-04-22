import logging
import requests

from config.settings import FATHOM_API_KEY, FATHOM_BASE_URL

logger = logging.getLogger(__name__)

_HEADERS = {"X-Api-Key": FATHOM_API_KEY}


def list_new_recordings(processed_ids: set[str]) -> list[dict]:
    """Returns unprocessed recordings from Fathom (all pages)."""
    recordings = []
    cursor = None

    while True:
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(f"{FATHOM_BASE_URL}/meetings", headers=_HEADERS, params=params)
        resp.raise_for_status()
        body = resp.json()

        for item in body.get("items", []):
            rec_id = str(item["recording_id"])
            if rec_id not in processed_ids:
                recordings.append(item)

        cursor = body.get("next_cursor")
        if not cursor:
            break

    return recordings


def get_transcript(recording_id: int | str) -> str:
    """Downloads and returns the transcript of a recording as plain text."""
    resp = requests.get(
        f"{FATHOM_BASE_URL}/recordings/{recording_id}/transcript",
        headers=_HEADERS,
    )
    resp.raise_for_status()
    segments = resp.json().get("transcript", [])

    lines = []
    for seg in segments:
        speaker = seg.get("speaker", {}).get("display_name", "Unknown")
        timestamp = seg.get("timestamp", "")
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{timestamp}] {speaker}: {text}")

    return "\n".join(lines)
