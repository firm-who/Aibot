"""
tools/docs.py — Google Docs operations for the AI agent.

Supports: read doc content, create docs, append text, replace text,
insert headings/tables, get document structure.
"""

from __future__ import annotations
from typing import Optional


def _get_docs_service(gmail_token: dict):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=gmail_token["access_token"],
        refresh_token=gmail_token.get("refresh_token"),
        token_uri=gmail_token.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=gmail_token.get("client_id"),
        client_secret=gmail_token.get("client_secret"),
    )
    return build("docs", "v1", credentials=creds)


def read_document(gmail_token: dict, document_id: str) -> dict:
    """
    Read full document content as plain text.
    Returns {title, content, word_count}.
    """
    service = _get_docs_service(gmail_token)
    doc = service.documents().get(documentId=document_id).execute()

    title = doc.get("title", "")
    content_parts = []

    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue
        line = ""
        for part in para.get("elements", []):
            text_run = part.get("textRun")
            if text_run:
                line += text_run.get("content", "")
        content_parts.append(line)

    content = "".join(content_parts)
    return {
        "title":      title,
        "content":    content,
        "word_count": len(content.split()),
        "url":        f"https://docs.google.com/document/d/{document_id}",
    }


def create_document(gmail_token: dict, title: str,
                    content: str = "",
                    heading: Optional[str] = None) -> dict:
    """
    Create a new Google Doc.
    Returns {id, title, url}.
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
    service = build("docs", "v1", credentials=creds)

    doc = service.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]

    requests = []

    # Insert heading if provided
    if heading:
        requests.append({
            "insertText": {"location": {"index": 1}, "text": heading + "\n"}
        })
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": 1, "endIndex": len(heading) + 1},
                "paragraphStyle": {"namedStyleType": "HEADING_1"},
                "fields": "namedStyleType",
            }
        })

    # Insert body content
    if content:
        insert_index = len(heading) + 2 if heading else 1
        requests.append({
            "insertText": {"location": {"index": insert_index}, "text": content}
        })

    if requests:
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()

    return {
        "id":    doc_id,
        "title": title,
        "url":   f"https://docs.google.com/document/d/{doc_id}",
    }


def append_to_document(gmail_token: dict, document_id: str,
                       text: str, as_heading: bool = False,
                       heading_level: int = 2) -> str:
    """
    Append text to end of a document.
    as_heading: if True, formats as a heading.
    heading_level: 1-6
    """
    service = _get_docs_service(gmail_token)

    # Get current doc to find end index
    doc = service.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    end_index = content[-1].get("endIndex", 1) - 1 if content else 1

    heading_map = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3",
                   4: "HEADING_4", 5: "HEADING_5", 6: "HEADING_6"}

    requests = [
        {"insertText": {"location": {"index": end_index}, "text": "\n" + text}}
    ]

    if as_heading:
        style = heading_map.get(heading_level, "HEADING_2")
        requests.append({
            "updateParagraphStyle": {
                "range": {
                    "startIndex": end_index + 1,
                    "endIndex":   end_index + 1 + len(text),
                },
                "paragraphStyle": {"namedStyleType": style},
                "fields": "namedStyleType",
            }
        })

    service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": requests},
    ).execute()

    return f"Text appended to document."


def replace_text(gmail_token: dict, document_id: str,
                 find: str, replace_with: str) -> dict:
    """
    Find and replace all occurrences of text in a document.
    Returns {occurrences_replaced}.
    """
    service = _get_docs_service(gmail_token)

    result = service.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": [{
                "replaceAllText": {
                    "containsText": {"text": find, "matchCase": False},
                    "replaceText":  replace_with,
                }
            }]
        },
    ).execute()

    replies = result.get("replies", [{}])
    count = replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0)
    return {"occurrences_replaced": count}


def insert_table(gmail_token: dict, document_id: str,
                 rows: int, columns: int,
                 data: Optional[list[list[str]]] = None) -> str:
    """
    Insert a table at the end of the document.
    data: optional 2D list of strings to fill the table.
    """
    service = _get_docs_service(gmail_token)

    doc = service.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    end_index = content[-1].get("endIndex", 1) - 1 if content else 1

    requests = [{
        "insertTable": {
            "rows":     rows,
            "columns":  columns,
            "location": {"index": end_index},
        }
    }]

    service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": requests},
    ).execute()

    return f"Table ({rows}×{columns}) inserted into document."


def get_document_outline(gmail_token: dict, document_id: str) -> dict:
    """
    Get document structure: title and all headings with their text.
    Useful for summarising large documents.
    """
    service = _get_docs_service(gmail_token)
    doc = service.documents().get(documentId=document_id).execute()

    title    = doc.get("title", "")
    headings = []

    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue
        style = para.get("paragraphStyle", {}).get("namedStyleType", "")
        if "HEADING" in style:
            text = "".join(
                part.get("textRun", {}).get("content", "")
                for part in para.get("elements", [])
            ).strip()
            if text:
                headings.append({"level": style, "text": text})

    return {"title": title, "headings": headings, "url": f"https://docs.google.com/document/d/{document_id}"}
