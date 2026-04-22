"""
Processes static knowledge documents from the docs/ folder into Notion.
Supports .txt and .md files.
Run: python scripts/ingest_docs.py

Each file is treated as a Training session authored by Federico.
Filename becomes the source reference.
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

from src.processor.extractor import extract_call_data
from src.notion.client import save_call

_DOCS_DIR = Path("docs")
_STATE_FILE = Path("processed_docs.json")
_SUPPORTED = {".txt", ".md"}


def _load_processed() -> set[str]:
    if _STATE_FILE.exists():
        return set(json.loads(_STATE_FILE.read_text()))
    return set()


def _save_processed(names: set[str]) -> None:
    _STATE_FILE.write_text(json.dumps(list(names)))


def run() -> int:
    processed = _load_processed()
    docs = [f for f in _DOCS_DIR.iterdir() if f.suffix in _SUPPORTED and f.name not in processed]

    if not docs:
        logger.info("Nessun nuovo documento trovato in docs/")
        return 0

    count = 0
    for doc in sorted(docs):
        logger.info(f"Elaborazione: {doc.name}")
        try:
            content = doc.read_text(encoding="utf-8")
            data = extract_call_data(content)
            # Documents are always Training sessions
            data.setdefault("tipo_sessione", "Training")
            page_url = save_call(doc.name, content, data)
            processed.add(doc.name)
            _save_processed(processed)
            count += 1
            logger.info(f"Salvato in Notion: {page_url}")
        except Exception as e:
            logger.error(f"Errore su {doc.name}: {e}")

    return count


if __name__ == "__main__":
    n = run()
    print(f"\nDocumenti elaborati: {n}")
