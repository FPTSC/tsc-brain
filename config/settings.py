import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL = "claude-sonnet-4-6"

FATHOM_API_KEY = os.environ["FATHOM_API_KEY"]
FATHOM_BASE_URL = "https://api.fathom.ai/external/v1"

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

APP_USER = os.getenv("APP_USER", "tsc")
APP_PASSWORD = os.getenv("APP_PASSWORD", "tsc2024")
