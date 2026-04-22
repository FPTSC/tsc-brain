import logging
from datetime import datetime
from notion_client import Client

from config.settings import NOTION_TOKEN, NOTION_DATABASE_ID
from src.vectorstore.client import index_page

logger = logging.getLogger(__name__)
_client = Client(auth=NOTION_TOKEN)


def _text(value: str | None) -> dict:
    return {"rich_text": [{"text": {"content": value or ""}}]}


def _title(value: str) -> dict:
    return {"title": [{"text": {"content": value}}]}


def _select(value: str | None) -> dict:
    if not value:
        return {"select": None}
    return {"select": {"name": value}}


def _multi_select(values: list[str]) -> dict:
    return {"multi_select": [{"name": v} for v in values if v]}


def _bullet_block(items: list[str]) -> list[dict]:
    return [
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content": item}}]},
        }
        for item in items
        if item
    ]


def _heading(text: str, level: int = 2) -> dict:
    h_type = f"heading_{level}"
    return {
        "object": "block",
        "type": h_type,
        h_type: {"rich_text": [{"text": {"content": text}}]},
    }


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": text}}]},
    }


def save_call(file_name: str, transcript: str, data: dict) -> str:
    """Creates a new knowledge page in the Notion database. Returns the page URL."""
    concetti = data.get("concetti_chiave") or []
    procedure = data.get("procedure") or []
    principi = data.get("principi") or []
    citazioni = data.get("citazioni_notevoli") or []
    errori = data.get("errori_rilevati") or []
    venditori = data.get("venditori_coinvolti") or []
    tags = data.get("tags") or []

    props = {
        "Titolo": _title(data.get("titolo") or file_name),
        "Categoria": _select(data.get("categoria")),
        "Sotto-categoria": _text(data.get("sotto_categoria")),
        "Tipo Sessione": _select(data.get("tipo_sessione")),
        "Pubblico": _select(data.get("pubblico")),
        "Sintesi": _text(data.get("sintesi")),
        "Venditori Coinvolti": _multi_select(venditori),
        "Tags": _multi_select(tags),
        "File Sorgente": _text(file_name),
        "Data Elaborazione": {"date": {"start": datetime.utcnow().isoformat()}},
    }

    children = []

    if errori:
        children.append(_heading("Errori Rilevati"))
        children.extend(_bullet_block(errori))

    if concetti:
        children.append(_heading("Concetti Chiave"))
        children.extend(_bullet_block(concetti))

    if procedure:
        children.append(_heading("Procedura / Framework"))
        children.extend(
            [
                {
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {"rich_text": [{"text": {"content": step}}]},
                }
                for step in procedure
                if step
            ]
        )

    if principi:
        children.append(_heading("Principi e Regole"))
        children.extend(_bullet_block(principi))

    if citazioni:
        children.append(_heading("Citazioni Notevoli"))
        children.extend(
            [
                {
                    "object": "block",
                    "type": "quote",
                    "quote": {"rich_text": [{"text": {"content": q}}]},
                }
                for q in citazioni
                if q
            ]
        )

    children.append(_heading("Trascrizione Originale"))
    chunk_size = 1900
    for i in range(0, min(len(transcript), 10000), chunk_size):
        children.append(_paragraph(transcript[i : i + chunk_size]))

    page = _client.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties=props,
        children=children,
    )

    url = page["url"]

    # Auto-index in vector store
    try:
        index_text = f"Titolo: {data.get('titolo') or file_name}\n"
        index_text += f"Categoria: {data.get('categoria') or ''}\n"
        index_text += f"Tipo Sessione: {data.get('tipo_sessione') or ''}\n"
        index_text += f"Sintesi: {data.get('sintesi') or ''}\n"
        index_text += "Concetti: " + "; ".join(concetti) + "\n"
        index_text += "Procedure: " + "; ".join(procedure) + "\n"
        index_text += "Principi: " + "; ".join(principi) + "\n"
        index_text += "Citazioni: " + "; ".join(citazioni) + "\n"
        index_text += "Errori: " + "; ".join(errori) + "\n"
        index_text += "Tags: " + ", ".join(tags)
        index_page(
            page_id=page["id"],
            text=index_text,
            metadata={
                "titolo": data.get("titolo") or file_name,
                "categoria": data.get("categoria") or "",
                "tipo_sessione": data.get("tipo_sessione") or "",
                "url": url,
            },
        )
    except Exception as e:
        logger.warning(f"Indicizzazione vettoriale fallita: {e}")

    return url
