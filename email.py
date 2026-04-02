"""
tools/email.py — Gmail send and read via Google API.

The gmail_token stored in Supabase is a JSON dict containing the OAuth2
credentials (access_token, refresh_token, token_uri, client_id, client_secret).
"""

from __future__ import annotations
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


def _get_gmail_service(gmail_token: dict):
    """Build authenticated Gmail service from stored token dict."""
    creds = Credentials(
        token=gmail_token.get("access_token"),
        refresh_token=gmail_token.get("refresh_token"),
        token_uri=gmail_token.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=gmail_token.get("client_id"),
        client_secret=gmail_token.get("client_secret"),
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    # Auto-refresh expired token
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def send_email(to: str, subject: str, body: str,
               gmail_token: dict) -> dict:
    """
    Send an email via Gmail.
    Returns {"success": True, "message_id": "..."} or {"success": False, "error": "..."}
    """
    try:
        service = _get_gmail_service(gmail_token)

        msg = MIMEMultipart()
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        return {"success": True, "message_id": result.get("id", "")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def read_emails(gmail_token: dict, max_results: int = 5) -> list[dict]:
    """
    Read recent unread emails from Gmail inbox.
    Returns list of dicts with keys: from, subject, snippet, date, id
    """
    try:
        service = _get_gmail_service(gmail_token)

        # Get list of unread message IDs
        results = service.users().messages().list(
            userId="me",
            q="is:unread in:inbox",
            maxResults=max_results,
        ).execute()

        messages = results.get("messages", [])
        emails   = []

        for msg_meta in messages:
            msg = service.users().messages().get(
                userId="me",
                id=msg_meta["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            emails.append({
                "id":      msg_meta["id"],
                "from":    headers.get("From", "unknown"),
                "subject": headers.get("Subject", "(no subject)"),
                "date":    headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })

        return emails
    except Exception as e:
        return [{"error": str(e)}]


def check_email_replies(original_subject: str, gmail_token: dict) -> list[dict]:
    """
    Check if anyone replied to an email with a given subject.
    Useful for the follow-up system: "did Tanaka-san reply to our invoice?"
    """
    try:
        service = _get_gmail_service(gmail_token)
        query   = f'subject:"{original_subject}" in:inbox'
        results = service.users().messages().list(
            userId="me", q=query, maxResults=3
        ).execute()
        messages = results.get("messages", [])
        return [{"id": m["id"]} for m in messages]
    except Exception as e:
        return [{"error": str(e)}]
