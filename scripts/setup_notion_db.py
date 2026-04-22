"""
Creates/updates all required properties in the Notion database.
Run after schema changes: python scripts/setup_notion_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from notion_client import Client
from config.settings import NOTION_TOKEN, NOTION_DATABASE_ID

PROPERTIES = {
    "Categoria": {
        "select": {
            "options": [
                {"name": "Vendita",              "color": "blue"},
                {"name": "Comunicazione",        "color": "purple"},
                {"name": "Gestione Obiezioni",   "color": "red"},
                {"name": "Team Management",      "color": "green"},
                {"name": "Scaling",              "color": "orange"},
                {"name": "Leadership",           "color": "yellow"},
                {"name": "Mindset",              "color": "pink"},
                {"name": "Recruiting",           "color": "brown"},
                {"name": "Altro",                "color": "gray"},
            ]
        }
    },
    "Tipo Sessione": {
        "select": {
            "options": [
                {"name": "Training",            "color": "blue"},
                {"name": "Roleplay",            "color": "purple"},
                {"name": "Correzione",          "color": "red"},
                {"name": "Briefing Operativo",  "color": "yellow"},
                {"name": "Altro",               "color": "gray"},
            ]
        }
    },
    "Pubblico": {
        "select": {
            "options": [
                {"name": "Team Commerciale", "color": "blue"},
                {"name": "Manager",          "color": "green"},
                {"name": "Corso",            "color": "purple"},
                {"name": "Interno TSC",      "color": "yellow"},
                {"name": "Altro",            "color": "gray"},
            ]
        }
    },
    "Sotto-categoria":     {"rich_text": {}},
    "Sintesi":             {"rich_text": {}},
    "Venditori Coinvolti": {"multi_select": {"options": []}},
    "Tags":                {"multi_select": {"options": []}},
    "File Sorgente":       {"rich_text": {}},
    "Data Elaborazione":   {"date": {}},
}

if __name__ == "__main__":
    client = Client(auth=NOTION_TOKEN)

    print(f"Configurazione database: {NOTION_DATABASE_ID}\n")

    db = client.databases.retrieve(database_id=NOTION_DATABASE_ID)
    existing = set(db.get("properties", {}).keys())
    print(f"Proprietà esistenti: {', '.join(existing)}\n")

    to_add = {k: v for k, v in PROPERTIES.items() if k not in existing}

    if not to_add:
        print("Tutte le proprietà sono già presenti.")
        sys.exit(0)

    print(f"Aggiungo: {', '.join(to_add.keys())}")
    client.databases.update(database_id=NOTION_DATABASE_ID, properties=to_add)
    print("\nDone. Database pronto.")
