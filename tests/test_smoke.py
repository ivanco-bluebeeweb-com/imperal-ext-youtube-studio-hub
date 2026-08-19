"""Smoke tests for YouTube Studio Hub: account lifecycle, channel/video
reads and writes, analytics queries, channel management (playlists,
comments), and the content-ideas pipeline (including the AI-generation
path via MockAI's default response).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import handlers as h
from models import (
    ChannelScoped, DeleteIdeaParams, DisconnectAccountParams,
    GenerateContentIdeasParams, GetChannelAnalyticsParams,
    GetTopVideosParams, GetTrafficSourcesParams, GetVideoAnalyticsParams,
    GetVideoParams, ListAccountsParams, ListChannelPlaylistsParams,
    ListChannelVideosParams, ListChannelsParams, ListCommentsParams,
    ListIdeasParams, ListPlaylistItemsParams, ModerateCommentParams,
    NoParams, ReplyCommentParams, SaveAppSettingParams, SaveIdeaParams,
    SetVideoThumbnailParams, SwitchAccountParams,
    UpdateIdeaStatusParams, UpdateVideoMetadataParams,
)

_CHANNEL_RAW = {
    "id": "UCabc123",
    "snippet": {"title": "Creator Channel", "description": "desc",
                "thumbnails": {"high": {"url": "https://img/high.jpg"}}},
    "statistics": {"subscriberCount": "1000", "videoCount": "42", "viewCount": "99999"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UUabc123"}},
}

_VIDEO_RAW = {
    "id": "vid1",
    "snippet": {"title": "My Video", "description": "d", "publishedAt": "2026-01-01T00:00:00Z",
                "tags": ["a", "b"], "categoryId": "22",
                "thumbnails": {"high": {"url": "https://img/v.jpg"}}},
    "statistics": {"viewCount": "500", "likeCount": "10", "commentCount": "2"},
    "status": {"privacyStatus": "public"},
    "contentDetails": {"duration": "PT5M"},
}


# --------------------------------------------------------------- accounts

@pytest.mark.asyncio
async def test_connect_account_requires_oauth_secrets(ctx):
    ctx.secrets = type(ctx.secrets)({})
    out = await h.connect_account(ctx, NoParams())
    assert out.status == "error"
    assert out.error_code == "GOOGLE_OAUTH_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_connect_account_returns_authorize_url(ctx):
    async def _fake_authorize_url(provider, **kwargs):
        return "https://accounts.google.com/authorize?x=1"
    ctx.oauth_authorize_url = _fake_authorize_url
    out = await h.connect_account(ctx, NoParams())
    assert out.status == "success"
    assert "accounts.google.com" in out.data.action


@pytest.mark.asyncio
async def test_list_accounts_reports_connected_state(ctx, account):
    out = await h.list_accounts(ctx, ListAccountsParams(refresh=False))
    assert out.status == "success"
    assert out.data.items[0].email == "creator@example.com"
    assert out.data.items[0].state == "connected"


@pytest.mark.asyncio
async def test_disconnect_account_removes_it(ctx, account):
    out = await h.disconnect_account(ctx, DisconnectAccountParams(account_id=account.id))
    assert out.status == "success"
    remaining = await ctx.store.query("youtube_accounts", limit=10)
    assert remaining.data == []


@pytest.mark.asyncio
async def test_switch_account_missing_reports_error(ctx):
    out = await h.switch_account(ctx, SwitchAccountParams(account="nobody@example.com"))
    assert out.status == "error"


@pytest.mark.asyncio
async def test_save_app_setting_round_trips(ctx):
    out = await h.save_app_setting(ctx, SaveAppSettingParams(ai_ideas_enabled=False))
    assert out.status == "success"
    assert out.data.enabled is False


# --------------------------------------------------------------- channels

@pytest.mark.asyncio
async def test_list_channels_maps_raw_items(ctx, account):
    ctx.http.push({"items": [_CHANNEL_RAW]})
    out = await h.list_channels(ctx, ListChannelsParams(account=""))
    assert out.status == "success"
    assert out.data.items[0].title == "Creator Channel"
    assert out.data.items[0].subscriber_count == 1000


@pytest.mark.asyncio
async def test_get_channel_not_found_is_error(ctx, account):
    ctx.http.push({"items": []})
    out = await h.get_channel(ctx, ChannelScoped(channel_id="UCabc123"))
    assert out.status == "error"


# ----------------------------------------------------------------- videos

@pytest.mark.asyncio
async def test_list_channel_videos_uses_uploads_playlist(ctx, account):
    ctx.http.push({"items": [_CHANNEL_RAW]})  # get_channel -> uploads playlist id
    ctx.http.push({"items": [{"contentDetails": {"videoId": "vid1"}}], "nextPageToken": ""})
    ctx.http.push({"items": [_VIDEO_RAW]})
    out = await h.list_channel_videos(ctx, ListChannelVideosParams(channel_id="UCabc123", limit=25, page_token=""))
    assert out.status == "success"
    assert out.data.items[0].video_id == "vid1"
    assert out.data.items[0].view_count == 500


@pytest.mark.asyncio
async def test_get_video_loads_metadata(ctx, account):
    ctx.http.push({"items": [_VIDEO_RAW]})
    out = await h.get_video(ctx, GetVideoParams(video_id="vid1"))
    assert out.status == "success"
    assert out.data.title == "My Video"
    assert out.data.duration == "PT5M"


@pytest.mark.asyncio
async def test_update_video_metadata_merges_only_given_fields(ctx, account):
    ctx.http.push({"items": [_VIDEO_RAW]})  # read current video first
    ctx.http.push({**_VIDEO_RAW, "snippet": {**_VIDEO_RAW["snippet"], "title": "New Title"}})
    out = await h.update_video_metadata(
        ctx, UpdateVideoMetadataParams(video_id="vid1", title="New Title"),
    )
    assert out.status == "success"
    assert out.data.title == "New Title"


@pytest.mark.asyncio
async def test_set_video_thumbnail_uploads_fetched_image(ctx, account):
    ctx.http.push(body=b"fake-bytes", status=200, headers={"content-type": "image/jpeg"})
    ctx.http.push({})
    out = await h.set_video_thumbnail(
        ctx, SetVideoThumbnailParams(video_id="vid1", image_url="https://example.com/thumb.jpg"),
    )
    assert out.status == "success"


# ---------- Part D3 (SCENARIO_TESTING_STANDARD.md): security / SSRF -------

@pytest.mark.asyncio
async def test_d3_set_video_thumbnail_refuses_private_ip_target(ctx, account):
    """image_url is fully user-supplied and, once fetched, gets uploaded as a
    PUBLIC video thumbnail -- an unguarded fetch here is a real SSRF with an
    exfiltration angle (internal/metadata response body ends up published).
    127.0.0.1 resolves locally with no network access needed, so this test
    is fully offline. No ctx.http mock is pushed -- if the guard regresses
    and this reaches ctx.http.get(), the test fails on a missing mock,
    which is itself a loud signal the refusal stopped happening."""
    out = await h.set_video_thumbnail(
        ctx, SetVideoThumbnailParams(video_id="vid1", image_url="http://127.0.0.1/secret"),
    )
    assert out.status == "error"
    assert "cannot be fetched" in (out.error or "").lower()


@pytest.mark.asyncio
async def test_d3_set_video_thumbnail_refuses_non_http_scheme(ctx, account):
    out = await h.set_video_thumbnail(
        ctx, SetVideoThumbnailParams(video_id="vid1", image_url="file:///etc/passwd"),
    )
    assert out.status == "error"


# -------------------------------------------------------------- analytics

@pytest.mark.asyncio
async def test_get_channel_analytics_reads_rows(ctx, account):
    ctx.http.push({"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": [["2026-01-01", 5]]})
    out = await h.get_channel_analytics(
        ctx, GetChannelAnalyticsParams(channel_id="UCabc123", start_date="2026-01-01", end_date="2026-01-31"),
    )
    assert out.status == "success"


@pytest.mark.asyncio
async def test_get_video_analytics_reads_rows(ctx, account):
    ctx.http.push({"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": [["2026-01-01", 5]]})
    out = await h.get_video_analytics(
        ctx, GetVideoAnalyticsParams(video_id="vid1", channel_id="UCabc123",
                                      start_date="2026-01-01", end_date="2026-01-31"),
    )
    assert out.status == "success"


@pytest.mark.asyncio
async def test_get_top_videos_reads_rows(ctx, account):
    ctx.http.push({"columnHeaders": [{"name": "video"}, {"name": "views"}], "rows": [["vid1", 500]]})
    out = await h.get_top_videos(
        ctx, GetTopVideosParams(channel_id="UCabc123", start_date="2026-01-01", end_date="2026-01-31", limit=10),
    )
    assert out.status == "success"


@pytest.mark.asyncio
async def test_get_traffic_sources_reads_rows(ctx, account):
    ctx.http.push({"columnHeaders": [{"name": "insightTrafficSourceType"}, {"name": "views"}],
                    "rows": [["SEARCH", 300]]})
    out = await h.get_traffic_sources(
        ctx, GetTrafficSourcesParams(channel_id="UCabc123", start_date="2026-01-01", end_date="2026-01-31"),
    )
    assert out.status == "success"


# ------------------------------------------------------- channel management

@pytest.mark.asyncio
async def test_list_channel_playlists(ctx, account):
    ctx.http.push({"items": [{"id": "PL1", "snippet": {"title": "Playlist"},
                               "contentDetails": {"itemCount": 3}, "status": {"privacyStatus": "public"}}]})
    out = await h.list_channel_playlists(ctx, ListChannelPlaylistsParams(channel_id="UCabc123"))
    assert out.status == "success"
    assert out.data.items[0].playlist_id == "PL1"


@pytest.mark.asyncio
async def test_list_playlist_items(ctx, account):
    ctx.http.push({"items": [{"id": "PI1", "snippet": {
        "position": 0, "title": "Item 1", "playlistId": "PL1",
        "resourceId": {"videoId": "vid1"},
    }}]})
    out = await h.list_playlist_items(ctx, ListPlaylistItemsParams(playlist_id="PL1"))
    assert out.status == "success"
    assert out.data.items[0].video_id == "vid1"


@pytest.mark.asyncio
async def test_list_comments(ctx, account):
    ctx.http.push({"items": [{
        "id": "ct1",
        "snippet": {"topLevelComment": {"snippet": {
            "authorDisplayName": "Fan", "textDisplay": "Great video!", "likeCount": 3,
            "publishedAt": "2026-01-01T00:00:00Z", "moderationStatus": "published",
        }}, "totalReplyCount": 0},
    }]})
    out = await h.list_comments(ctx, ListCommentsParams(video_id="vid1"))
    assert out.status == "success"
    assert out.data.items[0].text == "Great video!"


@pytest.mark.asyncio
async def test_reply_to_comment(ctx, account):
    ctx.http.push({"snippet": {"authorDisplayName": "Me", "textDisplay": "Thanks!",
                                "publishedAt": "2026-01-01T00:00:00Z"}})
    out = await h.reply_to_comment(ctx, ReplyCommentParams(comment_thread_id="ct1", text="Thanks!"))
    assert out.status == "success"


@pytest.mark.asyncio
async def test_moderate_comment(ctx, account):
    ctx.http.push({})
    out = await h.moderate_comment(ctx, ModerateCommentParams(comment_id="c1", status="rejected"))
    assert out.status == "success"
    assert out.data.action == "rejected"


# ----------------------------------------------------------- content ideas

@pytest.mark.asyncio
async def test_save_and_list_content_ideas(ctx):
    saved = await h.save_content_idea(
        ctx, SaveIdeaParams(channel_id="UCabc123", title="Idea 1", target_keyword="kw", rationale="why"),
    )
    assert saved.status == "success"
    listed = await h.list_content_ideas(ctx, ListIdeasParams(channel_id="UCabc123"))
    assert listed.status == "success"
    assert len(listed.data.items) == 1
    assert listed.data.items[0].title == "Idea 1"


@pytest.mark.asyncio
async def test_generate_content_ideas_uses_ai_default_response(ctx):
    out = await h.generate_content_ideas(
        ctx, GenerateContentIdeasParams(channel_id="UCabc123", channel_title="My Channel", count=1),
    )
    assert out.status == "success"
    assert len(out.data.items) >= 1


@pytest.mark.asyncio
async def test_update_idea_status_and_delete(ctx):
    saved = await h.save_content_idea(
        ctx, SaveIdeaParams(channel_id="UCabc123", title="Idea 1"),
    )
    idea_id = saved.data.id
    updated = await h.update_idea_status(ctx, UpdateIdeaStatusParams(idea_id=idea_id, status="planned"))
    assert updated.status == "success"
    assert updated.data.status == "planned"
    deleted = await h.delete_idea(ctx, DeleteIdeaParams(idea_id=idea_id))
    assert deleted.status == "success"
    remaining = await ctx.store.query("youtube_content_ideas", limit=10)
    assert remaining.data == []


@pytest.mark.asyncio
async def test_update_idea_status_missing_is_error(ctx):
    out = await h.update_idea_status(ctx, UpdateIdeaStatusParams(idea_id="nope", status="planned"))
    assert out.status == "error"


# ---------- Part D2 (SCENARIO_TESTING_STANDARD.md): idempotency ----------

@pytest.mark.asyncio
async def test_d2_double_delete_idea_second_call_fails_clean(ctx):
    """A retried delete_idea on an already-deleted idea must surface a clean
    error, not silently re-claim success -- ctx.store.delete() returns bool
    and the handler must actually check it (real bug found and fixed this
    pass: it previously ignored the return value and always said "deleted")."""
    saved = await h.save_content_idea(ctx, SaveIdeaParams(channel_id="UCabc123", title="Idea 1"))
    idea_id = saved.data.id
    first = await h.delete_idea(ctx, DeleteIdeaParams(idea_id=idea_id))
    assert first.status == "success"
    second = await h.delete_idea(ctx, DeleteIdeaParams(idea_id=idea_id))
    assert second.status == "error"
    assert second.error_code == "YOUTUBE_IDEA_NOT_FOUND"
