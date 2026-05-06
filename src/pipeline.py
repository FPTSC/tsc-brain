import json
import logging
import os
from pathlib import Path

from src.fathom.client import list_new_recordings, get_transcript
from src.processor.extractor import extract_call_data
from src.notion.client import save_call

logger = logging.getLogger(__name__)

# Store alongside users.json so it survives Railway deploys on the /data volume.
_DATA_DIR = Path(os.environ.get("USERS_FILE", str(Path(__file__).parent.parent / "users.json"))).parent
_STATE_FILE = _DATA_DIR / "processed_ids.json"


def _load_processed() -> set[str]:
    if _STATE_FILE.exists():
        return set(json.loads(_STATE_FILE.read_text()))
    return set()


def _save_processed(ids: set[str]) -> None:
    _STATE_FILE.write_text(json.dumps(list(ids)))


def run() -> int:
    """Runs the full pipeline. Returns the number of recordings processed."""
    processed = _load_processed()
    new_recordings = list_new_recordings(processed)

    if not new_recordings:
        logger.info("Nessuna nuova registrazione trovata.")
        return 0

    count = 0
    for rec in new_recordings:
        rec_id = str(rec["recording_id"])
        title = rec.get("title") or f"recording_{rec_id}"

        logger.info(f"Elaborazione: {title} (id={rec_id})")
        try:
            transcript = get_transcript(rec_id)
            if not transcript.strip():
                logger.warning(f"Trascrizione vuota per {title}, salto.")
                processed.add(rec_id)
                _save_processed(processed)
                continue

            data = extract_call_data(transcript)
            page_url = save_call(title, transcript, data)
            processed.add(rec_id)
            _save_processed(processed)
            count += 1
            logger.info(f"Salvato in Notion: {page_url}")
        except Exception as e:
            logger.error(f"Errore su {title}: {e}")

    return count
