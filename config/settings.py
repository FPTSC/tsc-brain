import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"

FATHOM_API_KEY = os.environ.get("FATHOM_API_KEY", "")
FATHOM_BASE_URL = "https://api.fathom.ai/external/v1"

VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

APP_USER = os.environ.get("APP_USER", "tsc")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "tsc2024")
