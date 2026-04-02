"""
tools/business_profile.py — Google Business Profile (formerly My Business) operations.

Supports: list locations, read reviews, reply to reviews, post updates,
upload photos (logo, cover, general), get insights.

Uses the My Business Account Management API + My Business Business Information API.
OAuth scope required: https://www.googleapis.com/auth/business.manage
"""

from __future__ import annotations
import io
import json
from typing import Optional


def _get_credentials(gmail_token: dict):
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=gmail_token["access_token"],
        refresh_token=gmail_token.get("refresh_token"),
        token_uri=gmail_token.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=gmail_token.get("client_id"),
        client_secret=gmail_token.get("client_secret"),
    )


def _authorized_session(gmail_token: dict):
    from google.auth.transport.requests import AuthorizedSession
    creds = _get_credentials(gmail_token)
    return AuthorizedSession(creds)


# Base URLs
_ACCOUNT_URL  = "https://mybusinessaccountmanagement.googleapis.com/v1"
_BUSINESS_URL = "https://mybusinessbusinessinformation.googleapis.com/v1"
_REVIEWS_URL  = "https://mybusiness.googleapis.com/v4"
_POSTS_URL    = "https://mybusiness.googleapis.com/v4"
_MEDIA_URL    = "https://mybusiness.googleapis.com/v4"


def list_accounts(gmail_token: dict) -> list[dict]:
    """List all Google Business accounts the user has access to."""
    session = _authorized_session(gmail_token)
    resp = session.get(f"{_ACCOUNT_URL}/accounts")
    resp.raise_for_status()
    data = resp.json()
    accounts = []
    for acc in data.get("accounts", []):
        accounts.append({
            "name":         acc.get("name", ""),
            "account_name": acc.get("accountName", ""),
            "type":         acc.get("type", ""),
            "state":        acc.get("verificationState", ""),
        })
    return accounts


def list_locations(gmail_token: dict, account_name: str) -> list[dict]:
    """
    List all business locations under an account.
    account_name: from list_accounts(), e.g. 'accounts/123456789'
    """
    session = _authorized_session(gmail_token)
    resp = session.get(
        f"{_BUSINESS_URL}/{account_name}/locations",
        params={"readMask": "name,title,websiteUri,phoneNumbers,categories,storefrontAddress,regularHours"}
    )
    resp.raise_for_status()
    data = resp.json()
    locations = []
    for loc in data.get("locations", []):
        locations.append({
            "name":    loc.get("name", ""),
            "title":   loc.get("title", ""),
            "website": loc.get("websiteUri", ""),
            "phone":   loc.get("phoneNumbers", {}).get("primaryPhone", ""),
            "address": loc.get("storefrontAddress", {}),
        })
    return locations


def get_reviews(gmail_token: dict, location_name: str,
                max_results: int = 10) -> list[dict]:
    """
    Get reviews for a business location.
    location_name: e.g. 'accounts/123/locations/456'
    """
    session = _authorized_session(gmail_token)
    resp = session.get(
        f"{_REVIEWS_URL}/{location_name}/reviews",
        params={"pageSize": max_results},
    )
    resp.raise_for_status()
    data = resp.json()

    reviews = []
    for r in data.get("reviews", []):
        reviews.append({
            "review_id":   r.get("reviewId", ""),
            "name":        r.get("reviewer", {}).get("displayName", "Anonymous"),
            "rating":      r.get("starRating", ""),
            "comment":     r.get("comment", ""),
            "create_time": r.get("createTime", ""),
            "replied":     bool(r.get("reviewReply")),
            "reply":       r.get("reviewReply", {}).get("comment", ""),
        })
    return reviews


def reply_to_review(gmail_token: dict, location_name: str,
                    review_id: str, reply_text: str) -> str:
    """
    Reply to a Google review.
    review_id: from get_reviews()
    """
    session = _authorized_session(gmail_token)
    resp = session.put(
        f"{_REVIEWS_URL}/{location_name}/reviews/{review_id}/reply",
        json={"comment": reply_text},
    )
    resp.raise_for_status()
    return f"Reply posted to review {review_id}."


def create_post(gmail_token: dict, location_name: str,
                summary: str, post_type: str = "STANDARD",
                action_type: Optional[str] = None,
                action_url: Optional[str] = None,
                event_title: Optional[str] = None,
                event_start: Optional[str] = None,
                event_end: Optional[str] = None,
                offer_coupon: Optional[str] = None) -> dict:
    """
    Create a Google Business post (update).
    post_type: 'STANDARD', 'EVENT', 'OFFER', 'PRODUCT'
    action_type: 'BOOK', 'ORDER', 'SHOP', 'LEARN_MORE', 'SIGN_UP', 'CALL'
    event_start/end: ISO datetime strings e.g. '2025-06-01T10:00:00Z'
    """
    session = _authorized_session(gmail_token)

    body: dict = {
        "languageCode": "en",
        "summary":      summary,
        "topicType":    post_type,
    }

    if action_type and action_url:
        body["callToAction"] = {"actionType": action_type, "url": action_url}

    if post_type == "EVENT" and event_title:
        body["event"] = {
            "title": event_title,
            "schedule": {
                "startDateTime": {"seconds": 0},
                "endDateTime":   {"seconds": 0},
            },
        }

    if post_type == "OFFER" and offer_coupon:
        body["offer"] = {"couponCode": offer_coupon}

    resp = session.post(
        f"{_POSTS_URL}/{location_name}/localPosts",
        json=body,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "post_name":  data.get("name", ""),
        "state":      data.get("state", ""),
        "create_time": data.get("createTime", ""),
        "search_url": data.get("searchUrl", ""),
    }


def upload_photo(gmail_token: dict, location_name: str,
                 image_bytes: bytes, category: str = "ADDITIONAL",
                 description: str = "") -> dict:
    """
    Upload a photo to Google Business Profile.
    category: 'PROFILE' (logo), 'COVER', 'EXTERIOR', 'INTERIOR',
              'PRODUCT', 'AT_WORK', 'FOOD_AND_DRINK', 'MENU',
              'COMMON_AREA', 'ROOMS', 'TEAMS', 'ADDITIONAL'
    image_bytes: raw bytes of JPEG or PNG image.
    Returns the created media item info.
    """
    import base64

    session = _authorized_session(gmail_token)

    # Step 1: Start upload session
    start_resp = session.post(
        f"{_MEDIA_URL}/{location_name}/media",
        json={
            "mediaFormat": "PHOTO",
            "locationAssociation": {"category": category},
            "description": description,
        },
    )
    start_resp.raise_for_status()
    media_item = start_resp.json()
    upload_url = media_item.get("uploadData", {}).get("uploadUrl", "")

    if not upload_url:
        # Fallback: try direct base64 upload via dataRef
        data_ref_resp = session.post(
            "https://mybusiness.googleapis.com/upload/v1/media:upload",
            headers={"Content-Type": "image/jpeg", "X-Goog-Upload-Protocol": "raw"},
            data=image_bytes,
        )
        data_ref_resp.raise_for_status()
        upload_token = data_ref_resp.json().get("uploadToken", "")

        final_resp = session.post(
            f"{_MEDIA_URL}/{location_name}/media",
            json={
                "mediaFormat": "PHOTO",
                "locationAssociation": {"category": category},
                "description": description,
                "dataRef": {"uploadToken": upload_token},
            },
        )
        final_resp.raise_for_status()
        return final_resp.json()

    # Step 2: Upload raw bytes to the upload URL
    upload_resp = session.post(
        upload_url,
        headers={"Content-Type": "image/jpeg"},
        data=image_bytes,
    )
    upload_resp.raise_for_status()

    return {
        "name":     media_item.get("name", ""),
        "category": category,
        "status":   "uploaded",
    }


def delete_post(gmail_token: dict, post_name: str) -> str:
    """Delete a Google Business post. post_name from create_post()."""
    session = _authorized_session(gmail_token)
    resp = session.delete(f"{_POSTS_URL}/{post_name}")
    resp.raise_for_status()
    return f"Post {post_name} deleted."


def get_location_insights(gmail_token: dict, location_name: str) -> dict:
    """
    Get basic insights for a location: views, searches, actions.
    Returns raw insights data.
    """
    session = _authorized_session(gmail_token)
    resp = session.post(
        f"{_POSTS_URL}/{location_name}:reportInsights",
        json={
            "locationNames": [location_name],
            "basicRequest": {
                "metricRequests": [
                    {"metric": "ALL", "options": ["AGGREGATED_DAILY"]},
                ],
                "timeRange": {
                    "startTime": "2024-01-01T00:00:00Z",
                    "endTime":   "2025-12-31T23:59:59Z",
                },
            },
        },
    )
    if resp.status_code != 200:
        return {"error": f"Insights unavailable: {resp.text}"}
    return resp.json()
