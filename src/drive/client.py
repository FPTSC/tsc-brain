import os
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

from config.settings import GOOGLE_CREDENTIALS_PATH, GOOGLE_TOKEN_PATH, GDRIVE_FOLDER_ID, GDRIVE_SCOPES


def get_credentials() -> Credentials:
    creds = None
    if os.path.exists(GOOGLE_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_PATH, GDRIVE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_PATH, GDRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(GOOGLE_TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    return creds


def list_new_transcripts(processed_ids: set[str]) -> list[dict]:
    """Returns list of unprocessed .txt/.docx files from the Drive folder."""
    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    query = f"'{GDRIVE_FOLDER_ID}' in parents and trashed=false and (mimeType='text/plain' or mimeType='application/vnd.google-apps.document')"
    results = service.files().list(q=query, fields="files(id, name, createdTime)").execute()
    files = results.get("files", [])

    return [f for f in files if f["id"] not in processed_ids]


def download_transcript(file_id: str, mime_type: str) -> str:
    """Downloads and returns the text content of a Drive file."""
    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    if mime_type == "application/vnd.google-apps.document":
        content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        return content.decode("utf-8")
    else:
        request = service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue().decode("utf-8")
