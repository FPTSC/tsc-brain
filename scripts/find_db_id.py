import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from notion_client import Client
from config.settings import NOTION_TOKEN, NOTION_DATABASE_ID

client = Client(auth=NOTION_TOKEN)

print(f"Cerco database dentro la pagina {NOTION_DATABASE_ID}...\n")
blocks = client.blocks.children.list(block_id=NOTION_DATABASE_ID)

for block in blocks["results"]:
    if block["type"] == "child_database":
        db_id = block["id"].replace("-", "")
        title = block["child_database"].get("title", "(senza titolo)")
        print(f"Database trovato: {title}")
        print(f"ID: {db_id}")
        print(f"\nAggiorna .env con: NOTION_DATABASE_ID={db_id}")
