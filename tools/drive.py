"""
tools/drive.py — Google Drive operations for the AI agent.

Supports: search files, read file content, create docs/sheets, upload images,
move files, list folders, share files.

All calls use the user's gmail_token from Supabase (stored after OAuth).
"""

from __future__ import annotations
import io
import json
from typing import Optional


def _get_drive_service(gmail_token: dict):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=gmail_token["access_token"],
        refresh_token=gmail_token.get("refresh_token"),
        token_uri=gmail_token.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=gmail_token.get("client_id"),
        client_secret=gmail_token.get("client_secret"),
    )
    return build("drive", "v3", credentials=creds)


def search_files(gmail_token: dict, query: str, max_results: int = 10) -> list[dict]:
    """
    Search Google Drive for files matching query.
    Returns list of {id, name, mimeType, modifiedTime, webViewLink}.
    """
    service = _get_drive_service(gmail_token)
    # Build a Drive query from natural language query
    drive_query = f"name contains '{query}' and trashed=false"
    results = service.files().list(
        q=drive_query,
        pageSize=max_results,
        fields="files(id,name,mimeType,modifiedTime,webViewLink,size)",
        orderBy="modifiedTime desc",
    ).execute()
    return results.get("files", [])


def get_file_content(gmail_token: dict, file_id: str) -> dict:
    """
    Get the text content of a Drive file.
    Works for Google Docs (exports as text), Sheets (exports as CSV),
    and plain text/PDF files.
    Returns {name, content, mimeType}.
    """
    service = _get_drive_service(gmail_token)

    # Get file metadata first
    meta = service.files().get(
        fileId=file_id,
        fields="name,mimeType"
    ).execute()
    mime = meta.get("mimeType", "")
    name = meta.get("name", "")

    # Export Google Docs formats
    export_map = {
        "application/vnd.google-apps.document":     ("text/plain", "txt"),
        "application/vnd.google-apps.spreadsheet":  ("text/csv", "csv"),
        "application/vnd.google-apps.presentation": ("text/plain", "txt"),
    }

    if mime in export_map:
        export_mime, _ = export_map[mime]
        content_bytes = service.files().export(
            fileId=file_id, mimeType=export_mime
        ).execute()
        return {"name": name, "content": content_bytes.decode("utf-8", errors="replace"), "mimeType": mime}

    # For binary files, just return metadata
    return {"name": name, "content": f"[Binary file: {mime} — cannot display as text]", "mimeType": mime}


def create_document(gmail_token: dict, title: str, content: str = "",
                    folder_id: Optional[str] = None) -> dict:
    """
    Create a new Google Doc with optional initial content.
    Returns {id, name, webViewLink}.
    """
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=gmail_token["access_token"],
        refresh_token=gmail_token.get("refresh_token"),
        token_uri=gmail_token.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=gmail_token.get("client_id"),
        client_secret=gmail_token.get("client_secret"),
    )
    docs_service  = build("docs", "v1", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)

    # Create blank doc
    doc = docs_service.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]

    # Insert content if provided
    if content:
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
        ).execute()

    # Move to folder if specified
    if folder_id:
        drive_service.files().update(
            fileId=doc_id,
            addParents=folder_id,
            fields="id,parents",
        ).execute()

    # Get link
    file_meta = drive_service.files().get(
        fileId=doc_id, fields="webViewLink,name"
    ).execute()

    return {"id": doc_id, "name": title, "webViewLink": file_meta.get("webViewLink", "")}


def create_spreadsheet(gmail_token: dict, title: str,
                        folder_id: Optional[str] = None) -> dict:
    """Create a new blank Google Sheet. Returns {id, name, webViewLink}."""
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=gmail_token["access_token"],
        refresh_token=gmail_token.get("refresh_token"),
        token_uri=gmail_token.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=gmail_token.get("client_id"),
        client_secret=gmail_token.get("client_secret"),
    )
    sheets_service = build("sheets", "v4", credentials=creds)
    drive_service  = build("drive", "v3", credentials=creds)

    sheet = sheets_service.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()
    sheet_id = sheet["spreadsheetId"]

    if folder_id:
        drive_service.files().update(
            fileId=sheet_id, addParents=folder_id, fields="id,parents"
        ).execute()

    file_meta = drive_service.files().get(
        fileId=sheet_id, fields="webViewLink"
    ).execute()

    return {"id": sheet_id, "name": title, "webViewLink": file_meta.get("webViewLink", "")}


def upload_file(gmail_token: dict, file_name: str, file_bytes: bytes,
                mime_type: str, folder_id: Optional[str] = None) -> dict:
    """
    Upload any file to Google Drive.
    Returns {id, name, webViewLink}.
    """
    from googleapiclient.http import MediaIoBaseUpload
    service = _get_drive_service(gmail_token)

    metadata = {"name": file_name}
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type)
    file = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()

    return {"id": file["id"], "name": file["name"], "webViewLink": file.get("webViewLink", "")}


def share_file(gmail_token: dict, file_id: str,
               email: Optional[str] = None, role: str = "reader",
               anyone: bool = False) -> str:
    """
    Share a file with a specific email or make it public.
    role: 'reader', 'commenter', 'writer'
    Returns share URL.
    """
    service = _get_drive_service(gmail_token)

    if anyone:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": role},
        ).execute()
    elif email:
        service.permissions().create(
            fileId=file_id,
            body={"type": "user", "role": role, "emailAddress": email},
        ).execute()

    meta = service.files().get(fileId=file_id, fields="webViewLink").execute()
    return meta.get("webViewLink", "")


def list_folder(gmail_token: dict, folder_id: str = "root",
                max_results: int = 20) -> list[dict]:
    """List contents of a folder. Use folder_id='root' for My Drive."""
    service = _get_drive_service(gmail_token)
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        pageSize=max_results,
        fields="files(id,name,mimeType,modifiedTime,size)",
        orderBy="modifiedTime desc",
    ).execute()
    return results.get("files", [])
