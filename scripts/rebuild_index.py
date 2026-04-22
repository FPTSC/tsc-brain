"""
Rebuilds the vector index from all pages in the Notion database.
Run: python scripts/rebuild_index.py

Safe to re-run: uses upsert, so existing pages are updated not duplicated.
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from notion_client import Client
from config.settings import NOTION_TOKEN, NOTION_DATABASE_ID
from src.vectorstore.client import index_page, count

_notion = Client(auth=NOTION_TOKEN)


def _get_text(rich_text: list) -> str:
    return "".join(r.get("text", {}).get("content", "") for r in rich_text)


def _page_to_text(page: dict) -> str:
    props = page["properties"]

    def prop_text(key):
        p = props.get(key, {})
        if "title" in p:
            return _get_text(p["title"])
        if "rich_text" in p:
            return _get_text(p["rich_text"])
        if "select" in p and p["select"]:
            return p["select"]["name"]
        if "multi_select" in p:
            return ", ".join(o["name"] for o in p["multi_select"])
        return ""

    parts = [
        f"Titolo: {prop_text('Titolo')}",
        f"Categoria: {prop_text('Categoria')}",
        f"Tipo Sessione: {prop_text('Tipo Sessione')}",
        f"Sotto-categoria: {prop_text('Sotto-categoria')}",
        f"Sintesi: {prop_text('Sintesi')}",
        f"Tags: {prop_text('Tags')}",
        f"Venditori Coinvolti: {prop_text('Venditori Coinvolti')}",
    ]

    # Read block content (concetti, procedure, principi, citazioni, errori)
    try:
        blocks = _notion.blocks.children.list(block_id=page["id"])
        block_lines = []
        for block in blocks["results"]:
            btype = block["type"]
            if btype in ("heading_2", "bulleted_list_item", "numbered_list_item", "quote", "paragraph"):
                rich = block[btype].get("rich_text", [])
                text = _get_text(rich)
                if text.strip() and "Trascrizione Originale" not in text:
                    block_lines.append(text)
        if block_lines:
            parts.append("\n".join(block_lines))
    except Exception:
        pass

    return "\n".join(p for p in parts if p.split(": ", 1)[-1].strip())


def run():
    logger.info("Lettura pagine da Notion...")
    pages = []
    cursor = None
    while True:
        kwargs = {"database_id": NOTION_DATABASE_ID}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _notion.databases.query(**kwargs)
        pages.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]

    logger.info(f"Trovate {len(pages)} pagine. Indicizzazione in corso...")

    for page in pages:
        props = page["properties"]
        title_rt = props.get("Titolo", {}).get("title", [])
        titolo = "".join(r.get("text", {}).get("content", "") for r in title_rt)
        categoria = (props.get("Categoria", {}).get("select") or {}).get("name", "")
        tipo = (props.get("Tipo Sessione", {}).get("select") or {}).get("name", "")

        text = _page_to_text(page)
        metadata = {
            "titolo": titolo,
            "categoria": categoria,
            "tipo_sessione": tipo,
            "url": page.get("url", ""),
        }
        index_page(page["id"], text, metadata)
        logger.info(f"  ✓ {titolo or page['id']}")

    logger.info(f"\nDone. Totale vettori nel database: {count()}")


if __name__ == "__main__":
    run()
