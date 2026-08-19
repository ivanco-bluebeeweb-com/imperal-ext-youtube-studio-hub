"""YouTube Data API v3 / YouTube Analytics API v2 HTTP funnel, token
refresh, and structured errors.

Mirrors Google Drive Connector's drive_client.py exactly: classify() maps
HTTP status/body to stable error codes, refresh_access_token() rotates an
expired OAuth token using the app-scoped client id/secret, and fail()
produces the {"ok": False, "code", "error", "retryable"} shape every
handler in this app returns on failure.
"""

from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
YOUTUBE_ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
YOUTUBE_UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"

ACCOUNT_MISSING = "YOUTUBE_ACCOUNT_MISSING"
ACCOUNT_AMBIGUOUS = "YOUTUBE_ACCOUNT_AMBIGUOUS"
TOKEN_REJECTED = "YOUTUBE_TOKEN_REJECTED"
NOT_FOUND = "YOUTUBE_NOT_FOUND"
VALIDATION_FAILED = "YOUTUBE_VALIDATION_FAILED"
RESPONSE_UNEXPECTED = "YOUTUBE_RESPONSE_UNEXPECTED"
UNREACHABLE = "YOUTUBE_UNREACHABLE"
QUOTA_EXCEEDED = "YOUTUBE_QUOTA_EXCEEDED"

_MESSAGES = {
    ACCOUNT_MISSING: "No Google account is connected yet.",
    ACCOUNT_AMBIGUOUS: "Several Google accounts are connected; name the account to use.",
    TOKEN_REJECTED: "Google rejected this connection. Reconnect the Google account and try again.",
    NOT_FOUND: "YouTube has no such channel/video, or this account cannot access it.",
    VALIDATION_FAILED: "YouTube rejected the request as invalid.",
    RESPONSE_UNEXPECTED: "YouTube returned a response the connector could not safely interpret.",
    UNREACHABLE: "Could not reach YouTube.",
    QUOTA_EXCEEDED: "YouTube API daily quota was exceeded; try again later.",
    "PERMISSION_DENIED": "This Google account is not allowed to access that item.",
    "RATE_LIMITED": "YouTube is rate-limiting requests; try again shortly.",
    "BACKEND_5XX": "YouTube returned a server error; try again shortly.",
    "BACKEND_TIMEOUT": "YouTube took too long to respond; try again shortly.",
}
_RETRYABLE = {"RATE_LIMITED", "BACKEND_5XX", "BACKEND_TIMEOUT", QUOTA_EXCEEDED}


def fail(code: str, message: str | None = None) -> dict:
    return {"ok": False, "code": code, "error": message or _MESSAGES.get(code, "YouTube request failed."),
            "retryable": code in _RETRYABLE}


def _body(resp):
    body = resp.body
    if isinstance(body, (str, bytes, bytearray)):
        try:
            return resp.json()
        except Exception:
            return body
    return body


def classify(status: int, body) -> dict:
    detail = ""
    reason = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
            errors = err.get("errors") or []
            if errors and isinstance(errors[0], dict):
                reason = str(errors[0].get("reason") or "")
    if status == 400:
        code = VALIDATION_FAILED
    elif status == 401:
        code = TOKEN_REJECTED
    elif status == 403:
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            code = QUOTA_EXCEEDED
        elif reason == "rateLimitExceeded" or "rate" in detail.lower():
            code = "RATE_LIMITED"
        else:
            code = "PERMISSION_DENIED"
    elif status == 404:
        code = NOT_FOUND
    elif status == 429:
        code = "RATE_LIMITED"
    elif 500 <= status < 600:
        code = "BACKEND_5XX"
    else:
        code = RESPONSE_UNEXPECTED
    message = _MESSAGES.get(code, "YouTube request failed.")
    if code == VALIDATION_FAILED and detail:
        message = f"YouTube rejected the request: {detail}"
    return fail(code, message)


def _timeout_code(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    return "BACKEND_TIMEOUT" if "timeout" in name or "timedout" in name else UNREACHABLE


async def refresh_access_token(ctx, account_doc) -> dict:
    """Refresh one saved OAuth account without exposing credentials."""
    data = account_doc.data or {}
    refresh_token = str(data.get("refresh_token") or "")
    if not refresh_token:
        return fail(TOKEN_REJECTED, "Google did not provide a refresh token. Reconnect the account and approve offline access.")
    try:
        client_id = await ctx.secrets.get("google_client_id")
        client_secret = await ctx.secrets.get("google_client_secret")
        if not client_id or not client_secret:
            return fail(TOKEN_REJECTED, "Google OAuth credentials are not configured for this app.")
        resp = await ctx.http.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
    except Exception as exc:
        return fail(_timeout_code(exc), str(exc))
    body = _body(resp)
    if resp.status_code != 200 or not isinstance(body, dict) or "access_token" not in body:
        return classify(resp.status_code, body if isinstance(body, dict) else {})
    access_token = str(body["access_token"])
    expires_in = int(body.get("expires_in") or 3600)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    await ctx.store.update("youtube_accounts", account_doc.id, {
        "access_token": access_token,
        "token_expires_at": expires_at,
    })
    return {"ok": True, "access_token": access_token}


async def valid_access_token(ctx, account_doc) -> dict:
    """Return a currently-valid access token, refreshing first if it expired."""
    data = account_doc.data or {}
    expires_at = str(data.get("token_expires_at") or "")
    access_token = str(data.get("access_token") or "")
    fresh = False
    if expires_at:
        try:
            fresh = datetime.fromisoformat(expires_at) > datetime.now(timezone.utc) + timedelta(seconds=60)
        except ValueError:
            fresh = False
    if access_token and fresh:
        return {"ok": True, "access_token": access_token}
    return await refresh_access_token(ctx, account_doc)


async def _dispatch(ctx, method: str, url: str, *, params=None, json_body=None, headers=None, content=None):
    """ctx.http has no generic .request() -- only named verb methods
    (get/post/put/patch/delete) -- so route by method name explicitly."""
    verb = method.upper()
    fn = {
        "GET": ctx.http.get, "POST": ctx.http.post, "PUT": ctx.http.put,
        "PATCH": ctx.http.patch, "DELETE": ctx.http.delete,
    }.get(verb)
    if fn is None:
        raise ValueError(f"Unsupported HTTP method: {method}")
    kwargs = {"headers": headers or {}}
    if params is not None:
        kwargs["params"] = params
    if json_body is not None:
        kwargs["json"] = json_body
    if content is not None:
        kwargs["content"] = content
    return await fn(url, **kwargs)


async def api_call(ctx, account_doc, method: str, url: str, *, params: dict | None = None,
                    json_body: dict | None = None, base: str = YOUTUBE_API) -> dict:
    """Single authorized call to the YouTube Data or Analytics API, with one
    retry after a token refresh if the first attempt is rejected as expired."""
    token_out = await valid_access_token(ctx, account_doc)
    if not token_out.get("ok"):
        return token_out
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {token_out['access_token']}"}
        full_url = url if url.startswith("http") else f"{base}{url}"
        try:
            resp = await _dispatch(ctx, method, full_url, params=params, json_body=json_body, headers=headers)
        except Exception as exc:
            return fail(_timeout_code(exc), str(exc))
        body = _body(resp)
        if resp.status_code < 300:
            return {"ok": True, "data": body if isinstance(body, dict) else {}}
        classified = classify(resp.status_code, body if isinstance(body, dict) else {})
        if classified["code"] == TOKEN_REJECTED and attempt == 0:
            token_out = await refresh_access_token(ctx, account_doc)
            if not token_out.get("ok"):
                return token_out
            continue
        return classified
    return classified


# ------------------------------------------------------------- data API v3

async def verify_account(ctx, account_doc) -> dict:
    """Confirm the token still works and return this account's own channel."""
    out = await api_call(ctx, account_doc, "GET", "/channels",
                          params={"part": "snippet", "mine": "true"})
    if not out.get("ok"):
        return out
    items = out["data"].get("items") or []
    return {"ok": True, "items": items}


async def list_my_channels(ctx, account_doc) -> dict:
    """List every channel this connected Google account owns (usually one,
    but Brand Accounts can own several)."""
    out = await api_call(ctx, account_doc, "GET", "/channels", params={
        "part": "snippet,statistics,contentDetails", "mine": "true", "maxResults": 50,
    })
    if not out.get("ok"):
        return out
    items = out["data"].get("items") or []
    return {"ok": True, "items": items}


async def get_channel(ctx, account_doc, channel_id: str) -> dict:
    out = await api_call(ctx, account_doc, "GET", "/channels", params={
        "part": "snippet,statistics,contentDetails", "id": channel_id,
    })
    if not out.get("ok"):
        return out
    items = out["data"].get("items") or []
    if not items:
        return fail(NOT_FOUND)
    return {"ok": True, "item": items[0]}


async def _uploads_playlist_id(ctx, account_doc, channel_id: str) -> dict:
    out = await get_channel(ctx, account_doc, channel_id)
    if not out.get("ok"):
        return out
    playlist_id = (
        (out["item"].get("contentDetails") or {}).get("relatedPlaylists") or {}
    ).get("uploads")
    if not playlist_id:
        return fail(NOT_FOUND, "This channel has no uploads playlist.")
    return {"ok": True, "playlist_id": playlist_id}


async def list_channel_videos(ctx, account_doc, channel_id: str, *, limit: int = 25, page_token: str = "") -> dict:
    """List a channel's uploaded videos via its uploads playlist (cheaper on
    quota than search.list, and works for videos of any privacy status the
    owning account can see)."""
    uploads = await _uploads_playlist_id(ctx, account_doc, channel_id)
    if not uploads.get("ok"):
        return uploads
    out = await api_call(ctx, account_doc, "GET", "/playlistItems", params={
        "part": "snippet,contentDetails", "playlistId": uploads["playlist_id"],
        "maxResults": limit, **({"pageToken": page_token} if page_token else {}),
    })
    if not out.get("ok"):
        return out
    video_ids = [
        str((item.get("contentDetails") or {}).get("videoId") or "")
        for item in (out["data"].get("items") or [])
    ]
    video_ids = [v for v in video_ids if v]
    if not video_ids:
        return {"ok": True, "items": [], "next_page_token": out["data"].get("nextPageToken", "")}
    videos_out = await api_call(ctx, account_doc, "GET", "/videos", params={
        "part": "snippet,statistics,status,contentDetails", "id": ",".join(video_ids),
    })
    if not videos_out.get("ok"):
        return videos_out
    return {"ok": True, "items": videos_out["data"].get("items") or [],
            "next_page_token": out["data"].get("nextPageToken", "")}


async def get_video(ctx, account_doc, video_id: str) -> dict:
    out = await api_call(ctx, account_doc, "GET", "/videos", params={
        "part": "snippet,statistics,status,contentDetails", "id": video_id,
    })
    if not out.get("ok"):
        return out
    items = out["data"].get("items") or []
    if not items:
        return fail(NOT_FOUND)
    return {"ok": True, "item": items[0]}


async def update_video_metadata(ctx, account_doc, params) -> dict:
    """Update title/description/tags/category/visibility. YouTube's videos.update
    requires the FULL snippet/status objects back (partial updates are not
    supported), so this reads the current video first and only overwrites the
    fields the caller actually set."""
    current = await get_video(ctx, account_doc, params.video_id)
    if not current.get("ok"):
        return current
    video = current["item"]
    snippet = dict(video.get("snippet") or {})
    status = dict(video.get("status") or {})
    if params.title:
        snippet["title"] = params.title
    if params.description:
        snippet["description"] = params.description
    if params.tags:
        snippet["tags"] = params.tags
    if params.category_id:
        snippet["categoryId"] = params.category_id
    if params.visibility:
        status["privacyStatus"] = params.visibility
    body = {"id": params.video_id, "snippet": snippet, "status": status}
    out = await api_call(ctx, account_doc, "PUT", "/videos", params={"part": "snippet,status"}, json_body=body)
    if not out.get("ok"):
        return out
    return {"ok": True, "item": out["data"]}


def _check_host_is_public(host: str) -> str | None:
    """Resolve host and return a refusal reason, or None if it's safe to fetch.

    Same SSRF guard as SEO Audit Engine's seoaudit/fetcher.py -- checks EVERY
    resolved address (A and AAAA); if even one is private/loopback/link-local/
    reserved/multicast, the whole host is refused rather than just that one IP,
    since a multi-answer DNS response doesn't let us pick which IP the
    following connect() will actually use. Known residual risk (same as that
    file): DNS rebinding between this check and the actual connect() is not
    closed by this guard.
    """
    hostname = host.split(":")[0].strip("[]")
    if not hostname:
        return "empty host"
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return f"host does not resolve: {e}"
    for info in infos:
        raw_ip = info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return f"address {raw_ip} is private/internal -- refusing to fetch it"
    return None


async def set_video_thumbnail(ctx, account_doc, video_id: str, image_url: str) -> dict:
    """Fetch the given https:// image and upload it as the video's custom
    thumbnail via the upload endpoint's media-only mode.

    SSRF guard: image_url is fully user-supplied and, once fetched, its bytes
    get uploaded as a PUBLIC video thumbnail -- so this isn't just a
    server-hangs-itself risk like a plain fetcher, it's a potential
    exfiltration path (fetch an internal/metadata endpoint, publish its
    response body as a public thumbnail). Refuse private/loopback/link-local/
    reserved targets before ever calling ctx.http.get() on them.
    """
    parsed = urlparse(image_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return fail(VALIDATION_FAILED, "image_url must be a public http(s):// URL.")
    refusal = _check_host_is_public(parsed.hostname)
    if refusal:
        return fail(VALIDATION_FAILED, f"That image URL cannot be fetched ({refusal}).")
    try:
        img_resp = await ctx.http.get(image_url)
    except Exception as exc:
        return fail(_timeout_code(exc), str(exc))
    if img_resp.status_code >= 300:
        return fail(VALIDATION_FAILED, "Could not download the thumbnail image from that URL.")
    content_type = img_resp.headers.get("content-type", "image/jpeg") if hasattr(img_resp, "headers") else "image/jpeg"
    token_out = await valid_access_token(ctx, account_doc)
    if not token_out.get("ok"):
        return token_out
    try:
        resp = await _dispatch(
            ctx, "POST", f"{YOUTUBE_UPLOAD_API}/thumbnails/set",
            params={"videoId": video_id, "uploadType": "media"},
            headers={"Authorization": f"Bearer {token_out['access_token']}", "Content-Type": content_type},
            content=img_resp.content if hasattr(img_resp, "content") else img_resp.body,
        )
    except Exception as exc:
        return fail(_timeout_code(exc), str(exc))
    body = _body(resp)
    if resp.status_code >= 300:
        return classify(resp.status_code, body if isinstance(body, dict) else {})
    return {"ok": True, "item": body if isinstance(body, dict) else {}}


async def list_channel_playlists(ctx, account_doc, channel_id: str) -> dict:
    out = await api_call(ctx, account_doc, "GET", "/playlists", params={
        "part": "snippet,contentDetails", "channelId": channel_id, "maxResults": 50,
    })
    if not out.get("ok"):
        return out
    return {"ok": True, "items": out["data"].get("items") or []}


async def list_playlist_items(ctx, account_doc, playlist_id: str) -> dict:
    out = await api_call(ctx, account_doc, "GET", "/playlistItems", params={
        "part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": 50,
    })
    if not out.get("ok"):
        return out
    return {"ok": True, "items": out["data"].get("items") or []}


async def list_comments(ctx, account_doc, video_id: str) -> dict:
    out = await api_call(ctx, account_doc, "GET", "/commentThreads", params={
        "part": "snippet,replies", "videoId": video_id, "maxResults": 50, "order": "time",
    })
    if not out.get("ok"):
        return out
    return {"ok": True, "items": out["data"].get("items") or []}


async def reply_to_comment(ctx, account_doc, comment_thread_id: str, text: str) -> dict:
    out = await api_call(ctx, account_doc, "POST", "/comments", params={"part": "snippet"}, json_body={
        "snippet": {"parentId": comment_thread_id, "textOriginal": text},
    })
    if not out.get("ok"):
        return out
    return {"ok": True, "item": out["data"]}


async def moderate_comment(ctx, account_doc, comment_id: str, status: str) -> dict:
    """status: 'published' (heldForReview=False), 'heldForReview', or 'rejected'."""
    out = await api_call(ctx, account_doc, "POST", "/comments/setModerationStatus", params={
        "id": comment_id, "moderationStatus": status,
    })
    if not out.get("ok"):
        return out
    return {"ok": True, "item": {"id": comment_id, "status": status}}


# --------------------------------------------------------- Analytics API v2

async def query_analytics(ctx, account_doc, *, channel_id: str, start_date: str, end_date: str,
                           metrics: str, dimensions: str = "", filters: str = "",
                           sort: str = "", max_results: int = 0) -> dict:
    params = {
        "ids": f"channel=={channel_id}",
        "startDate": start_date,
        "endDate": end_date,
        "metrics": metrics,
    }
    if dimensions:
        params["dimensions"] = dimensions
    if filters:
        params["filters"] = filters
    if sort:
        params["sort"] = sort
    if max_results:
        params["maxResults"] = max_results
    out = await api_call(ctx, account_doc, "GET", "", params=params, base=YOUTUBE_ANALYTICS_API)
    if not out.get("ok"):
        return out
    return {"ok": True, **out["data"]}
