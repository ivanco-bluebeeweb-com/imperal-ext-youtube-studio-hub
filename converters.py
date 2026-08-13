"""Pure conversion of raw YouTube Data API v3 / Analytics API v2 JSON into
this app's SDL entities. No I/O here -- leaf module, imported one-way from
main.py, same separation as content-strategy-app's converters.py."""

from __future__ import annotations

from models import AnalyticsReport, AnalyticsRow, Channel, Comment, Playlist, PlaylistItem, Video

_ISO8601_DURATION_RE = None  # populated lazily to avoid import cost if unused


def _pick_thumbnail(thumbnails: dict) -> str:
    for key in ("high", "medium", "default"):
        entry = (thumbnails or {}).get(key)
        if isinstance(entry, dict) and entry.get("url"):
            return str(entry["url"])
    return ""


def to_channel(raw: dict, account_email: str = "") -> Channel:
    snippet = raw.get("snippet") or {}
    stats = raw.get("statistics") or {}
    channel_id = str(raw.get("id") or "")
    return Channel(
        id=channel_id,
        channel_id=channel_id,
        title=str(snippet.get("title") or "Untitled channel"),
        description=str(snippet.get("description") or ""),
        thumbnail_url=_pick_thumbnail(snippet.get("thumbnails") or {}),
        subscriber_count=int(stats.get("subscriberCount") or 0),
        video_count=int(stats.get("videoCount") or 0),
        view_count=int(stats.get("viewCount") or 0),
        channel_url=f"https://www.youtube.com/channel/{channel_id}" if channel_id else "",
        account_email=account_email,
    )


def to_video(raw: dict, channel_id: str = "") -> Video:
    """Accepts either a videos.list item (has snippet/statistics/status/
    contentDetails top-level) or a search.list item (snippet only, id.videoId
    instead of id)."""
    vid = raw.get("id")
    video_id = vid.get("videoId") if isinstance(vid, dict) else str(vid or "")
    snippet = raw.get("snippet") or {}
    stats = raw.get("statistics") or {}
    status = raw.get("status") or {}
    content_details = raw.get("contentDetails") or {}
    return Video(
        id=video_id,
        video_id=video_id,
        channel_id=str(snippet.get("channelId") or channel_id),
        title=str(snippet.get("title") or "Untitled video"),
        description=str(snippet.get("description") or ""),
        thumbnail_url=_pick_thumbnail(snippet.get("thumbnails") or {}),
        published_at=str(snippet.get("publishedAt") or ""),
        duration=str(content_details.get("duration") or ""),
        visibility=str(status.get("privacyStatus") or ""),
        view_count=int(stats.get("viewCount") or 0),
        like_count=int(stats.get("likeCount") or 0),
        comment_count=int(stats.get("commentCount") or 0),
        tags=list(snippet.get("tags") or []),
        category_id=str(snippet.get("categoryId") or ""),
        video_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
    )


def to_playlist(raw: dict, channel_id: str = "") -> Playlist:
    snippet = raw.get("snippet") or {}
    status = raw.get("status") or {}
    content_details = raw.get("contentDetails") or {}
    playlist_id = str(raw.get("id") or "")
    return Playlist(
        id=playlist_id,
        playlist_id=playlist_id,
        channel_id=str(snippet.get("channelId") or channel_id),
        title=str(snippet.get("title") or "Untitled playlist"),
        description=str(snippet.get("description") or ""),
        item_count=int(content_details.get("itemCount") or 0),
        visibility=str(status.get("privacyStatus") or ""),
        playlist_url=f"https://www.youtube.com/playlist?list={playlist_id}" if playlist_id else "",
    )


def to_playlist_item(raw: dict, playlist_id: str = "") -> PlaylistItem:
    snippet = raw.get("snippet") or {}
    resource = snippet.get("resourceId") or {}
    return PlaylistItem(
        id=str(raw.get("id") or ""),
        playlist_item_id=str(raw.get("id") or ""),
        playlist_id=str(snippet.get("playlistId") or playlist_id),
        video_id=str(resource.get("videoId") or ""),
        title=str(snippet.get("title") or ""),
        position=int(snippet.get("position") or 0),
    )


def to_comment(raw: dict, video_id: str = "") -> Comment:
    """Accepts a commentThreads.list item (top-level comment nested one level)."""
    top = ((raw.get("snippet") or {}).get("topLevelComment") or {})
    snippet = (top.get("snippet") or {})
    thread_snippet = raw.get("snippet") or {}
    return Comment(
        id=str(raw.get("id") or top.get("id") or ""),
        title=str(snippet.get("textDisplay") or "")[:80] or "Comment",
        comment_id=str(raw.get("id") or top.get("id") or ""),
        video_id=str(thread_snippet.get("videoId") or video_id),
        author=str(snippet.get("authorDisplayName") or ""),
        author_channel_id=str((snippet.get("authorChannelId") or {}).get("value") or ""),
        text=str(snippet.get("textDisplay") or ""),
        like_count=int(snippet.get("likeCount") or 0),
        published_at=str(snippet.get("publishedAt") or ""),
        moderation_status=str(snippet.get("moderationStatus") or "published"),
        reply_count=int(thread_snippet.get("totalReplyCount") or 0),
    )


def to_analytics_report(raw: dict, *, channel_id: str = "", video_id: str = "",
                         start_date: str = "", end_date: str = "",
                         requested_metrics: list[str] | None = None) -> AnalyticsReport:
    """Converts a YouTube Analytics API reports.query response. `columnHeaders`
    tells us which columns are dimensions vs metrics; `rows` are plain lists
    in that column order -- we split each row using the header count."""
    headers = raw.get("columnHeaders") or []
    dim_count = sum(1 for h in headers if h.get("columnType") == "DIMENSION")
    rows_out = []
    for row in raw.get("rows") or []:
        dims = [str(v) for v in row[:dim_count]]
        metrics_vals = []
        for v in row[dim_count:]:
            try:
                metrics_vals.append(float(v))
            except (TypeError, ValueError):
                metrics_vals.append(0.0)
        rows_out.append(AnalyticsRow(dimension_values=dims, metric_values=metrics_vals))
    metric_names = [h.get("name", "") for h in headers if h.get("columnType") == "METRIC"] or (requested_metrics or [])
    dimension_names = [h.get("name", "") for h in headers if h.get("columnType") == "DIMENSION"]
    monetary_available = any(
        "revenue" in m.lower() or "cpm" in m.lower() or "playback" in m.lower() and "monet" in m.lower()
        for m in metric_names
    )
    return AnalyticsReport(
        id=f"{channel_id or video_id}:{start_date}:{end_date}",
        title=f"Analytics {start_date}–{end_date}",
        channel_id=channel_id,
        video_id=video_id,
        start_date=start_date,
        end_date=end_date,
        dimensions=dimension_names,
        metrics=metric_names,
        rows=rows_out,
        monetary_data_available=monetary_available,
    )
