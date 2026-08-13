"""Chat functions for YouTube Studio Hub.

Every function returns ActionResult via the same _success/_error helper
pattern as Google Drive Connector's handlers.py. No video upload/publish,
no trim/edit -- see PREPARATION.md and app.py docstring for boundaries.
"""

from __future__ import annotations

from imperal_sdk import ActionResult
from app import chat, ext
import accounts
import youtube_client as yc
from converters import (
    to_analytics_report, to_channel, to_comment, to_playlist,
    to_playlist_item, to_video,
)
from models import (
    AccountList, Account, AnalyticsReport, ChannelList,
    ChannelScoped, Channel, Comment, CommentList,
    DeleteIdeaParams, DisconnectAccountParams, GenerateContentIdeasParams,
    GetChannelAnalyticsParams, GetTopVideosParams, GetTrafficSourcesParams,
    GetVideoAnalyticsParams, GetVideoParams, Idea, IdeaList,
    ListAccountsParams, ListChannelPlaylistsParams,
    ListChannelVideosParams, ListChannelsParams, ListCommentsParams,
    ListIdeasParams, ListPlaylistItemsParams, ModerateCommentParams,
    NoParams, Playlist, PlaylistItemList, PlaylistList, PlaylistScoped,
    ReplyCommentParams, SaveAppSettingParams, SaveIdeaParams,
    SetVideoThumbnailParams, SettingResult, SwitchAccountParams,
    UpdateIdeaStatusParams, UpdateVideoMetadataParams, Video, VideoList,
)

IDEAS = "youtube_content_ideas"


def _error(out: dict):
    from imperal_sdk import ActionResult
    return ActionResult.error(
        str(out.get("error") or "Something went wrong."),
        retryable=bool(out.get("retryable", False)),
        code=str(out.get("code") or "YOUTUBE_ERROR"),
    )


def _success(data, message: str, refresh_panels: list[str] | None = None):
    from imperal_sdk import ActionResult
    return ActionResult.success(data, message, refresh_panels=refresh_panels or [])


async def _resolved(ctx, reference: str = ""):
    return await accounts.resolve_account(ctx, reference)


# ---------------------------------------------------------------- accounts

@chat.function("connect_account", "Get the Google authorization link to connect a YouTube account.",
               action_type="write", effects=["account.oauth.start"],
               event="youtube-studio-hub.account.updated", data_model=SettingResult)
async def connect_account(ctx, params: NoParams) -> "ActionResult":
    """Get the Google authorization link to connect a YouTube account."""
    client_id = await ctx.secrets.get("google_client_id")
    client_secret = await ctx.secrets.get("google_client_secret")
    if not client_id or not client_secret:
        from imperal_sdk import ActionResult
        return ActionResult.error(
            "Google OAuth is not configured for this app yet. An admin must set google_client_id and google_client_secret in Secrets.",
            retryable=False, code="GOOGLE_OAUTH_NOT_CONFIGURED",
        )
    url = await ctx.oauth_authorize_url("google")
    return _success(SettingResult(id="google", title="Google OAuth", account="", enabled=True, action=url),
                     "Open the Google authorization link to connect a YouTube account.")


@chat.function("list_accounts", "List connected Google accounts and connection state.",
               action_type="read", data_model=AccountList)
async def list_accounts(ctx, params: ListAccountsParams) -> "ActionResult":
    """List connected Google accounts and connection state."""
    rows = []
    for doc in await accounts.all_accounts(ctx):
        email = accounts.account_email(doc)
        label = accounts.account_label(doc)
        state = "connected"
        if accounts.identity_missing(doc):
            state = "reconnect_required"
            email = ""
        elif params.refresh:
            verified = await yc.verify_account(ctx, doc)
            state = "connected" if verified.get("ok") else "error"
        rows.append(Account(id=doc.id, title=label, email=email, active=bool((doc.data or {}).get("is_active")), state=state))
    return _success(AccountList(items=rows), f"{len(rows)} connected Google account(s).")


@chat.function("disconnect_account", "Disconnect a Google account. Its local YouTube Studio Hub data (saved ideas) is removed; nothing on YouTube itself changes.",
               action_type="write", effects=["account.delete"],
               event="youtube-studio-hub.account.updated", data_model=SettingResult)
async def disconnect_account(ctx, params: DisconnectAccountParams) -> "ActionResult":
    """Disconnect a Google account. Its local YouTube Studio Hub data (saved ideas) is removed; nothing on YouTube itself changes."""
    out = await accounts.disconnect(ctx, params.account_id)
    if not out.get("ok"):
        return _error(out)
    return _success(SettingResult(id=params.account_id, title=out.get("label", ""), account="", enabled=False, action="disconnected"),
                     f"Disconnected {out.get('label', 'the account')}. No YouTube data was changed.", ["yt_nav", "yt_center"])


@chat.function("switch_account", "Change the active Google account used by default.",
               action_type="write", effects=["account.active.update"],
               event="youtube-studio-hub.account.updated", data_model=SettingResult)
async def switch_account(ctx, params: SwitchAccountParams) -> "ActionResult":
    """Change the active Google account used by default."""
    out = await accounts.activate(ctx, params.account)
    if not out.get("ok"):
        return _error(out)
    email = accounts.account_email(out["account"])
    return _success(SettingResult(id=email, title=email, account=email, enabled=True, action="activated"),
                     f"{email} is now the active Google account.", ["yt_nav"])


@chat.function("save_app_setting", "Save an app-wide YouTube Studio Hub setting (e.g. whether Content Ideas may use AI generation).",
               action_type="write", effects=["setting.update"],
               event="youtube-studio-hub.setting.updated", data_model=SettingResult)
async def save_app_setting(ctx, params: SaveAppSettingParams) -> "ActionResult":
    """Save an app-wide YouTube Studio Hub setting (e.g. whether Content Ideas may use AI generation)."""
    saved = await accounts.save_app_setting(ctx, ai_ideas_enabled=params.ai_ideas_enabled)
    return _success(
        SettingResult(id="app", title="App settings", account="", enabled=bool(saved.get("ai_ideas_enabled", True)), action="saved"),
        "Settings saved.", ["yt_nav"],
    )


# ----------------------------------------------------------------- channels

@chat.function("list_channels", "List every YouTube channel reachable from connected Google account(s).",
               action_type="read", data_model=ChannelList)
async def list_channels(ctx, params: ListChannelsParams) -> "ActionResult":
    """List every YouTube channel reachable from connected Google account(s)."""
    resolved = await _resolved(ctx, params.account)
    if not resolved.get("ok"):
        return _error(resolved)
    doc = resolved["account"]
    out = await yc.list_my_channels(ctx, doc)
    if not out.get("ok"):
        return _error(out)
    email = accounts.account_email(doc)
    rows = [to_channel(raw, account_email=email) for raw in out["items"]]
    return _success(ChannelList(items=rows), f"{len(rows)} channel(s) on {email or 'this account'}.")


@chat.function("get_channel", "Read one YouTube channel's profile and stats.",
               action_type="read", data_model=Channel)
async def get_channel(ctx, params: ChannelScoped) -> "ActionResult":
    """Read one YouTube channel's profile and stats."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.get_channel(ctx, resolved["account"], params.channel_id)
    if not out.get("ok"):
        return _error(out)
    return _success(to_channel(out["item"]), "Channel loaded.")


# ------------------------------------------------------------------- videos

@chat.function("list_channel_videos", "List videos uploaded to one YouTube channel (My Content tab).",
               action_type="read", data_model=VideoList)
async def list_channel_videos(ctx, params: ListChannelVideosParams) -> "ActionResult":
    """List videos uploaded to one YouTube channel (My Content tab)."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.list_channel_videos(ctx, resolved["account"], params.channel_id, limit=params.limit, page_token=params.page_token)
    if not out.get("ok"):
        return _error(out)
    rows = [to_video(raw, channel_id=params.channel_id) for raw in out["items"]]
    result = VideoList(items=rows, next_page_token=out.get("next_page_token", ""))
    return _success(result, f"{len(rows)} video(s) on this channel.")


@chat.function("get_video", "Read one video's full metadata and stats.",
               action_type="read", data_model=Video)
async def get_video(ctx, params: GetVideoParams) -> "ActionResult":
    """Read one video's full metadata and stats."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.get_video(ctx, resolved["account"], params.video_id)
    if not out.get("ok"):
        return _error(out)
    return _success(to_video(out["item"]), "Video loaded.")


@chat.function("update_video_metadata", "Update a video's title, description, tags, category, or visibility (no editing/trimming of the video file itself).",
               action_type="write", effects=["video.metadata.update"],
               event="youtube-studio-hub.video.updated", data_model=Video)
async def update_video_metadata(ctx, params: UpdateVideoMetadataParams) -> "ActionResult":
    """Update a video's title, description, tags, category, or visibility (no editing/trimming of the video file itself)."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.update_video_metadata(ctx, resolved["account"], params)
    if not out.get("ok"):
        return _error(out)
    return _success(to_video(out["item"]), "Video metadata updated.", ["yt_center"])


@chat.function("set_video_thumbnail", "Replace a video's custom thumbnail image.",
               action_type="write", effects=["video.thumbnail.update"],
               event="youtube-studio-hub.video.updated", data_model=SettingResult)
async def set_video_thumbnail(ctx, params: SetVideoThumbnailParams) -> "ActionResult":
    """Replace a video's custom thumbnail image."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.set_video_thumbnail(ctx, resolved["account"], params.video_id, params.image_url)
    if not out.get("ok"):
        return _error(out)
    return _success(SettingResult(id=params.video_id, title="Thumbnail", account="", enabled=True, action="updated"),
                     "Thumbnail updated.", ["yt_center"])


# ---------------------------------------------------------------- analytics

@chat.function("get_channel_analytics", "Read channel-level YouTube Analytics: views, watch time, subscribers gained/lost, over a date range.",
               action_type="read", data_model=AnalyticsReport)
async def get_channel_analytics(ctx, params: GetChannelAnalyticsParams) -> "ActionResult":
    """Read channel-level YouTube Analytics: views, watch time, subscribers gained/lost, over a date range."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.query_analytics(
        ctx, resolved["account"], channel_id=params.channel_id,
        start_date=params.start_date, end_date=params.end_date,
        metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost,averageViewDuration",
        dimensions="day",
    )
    if not out.get("ok"):
        return _error(out)
    return _success(to_analytics_report(out), "Channel analytics loaded.")


@chat.function("get_video_analytics", "Read video-level YouTube Analytics: views, watch time, average view duration, likes, for one video.",
               action_type="read", data_model=AnalyticsReport)
async def get_video_analytics(ctx, params: GetVideoAnalyticsParams) -> "ActionResult":
    """Read video-level YouTube Analytics: views, watch time, average view duration, likes, for one video."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.query_analytics(
        ctx, resolved["account"], channel_id=params.channel_id,
        start_date=params.start_date, end_date=params.end_date,
        metrics="views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares",
        dimensions="day", filters=f"video=={params.video_id}",
    )
    if not out.get("ok"):
        return _error(out)
    return _success(to_analytics_report(out), "Video analytics loaded.")


@chat.function("get_top_videos", "Read a channel's top videos by views over a date range -- helps spot what is/isn't growing.",
               action_type="read", data_model=AnalyticsReport)
async def get_top_videos(ctx, params: GetTopVideosParams) -> "ActionResult":
    """Read a channel's top videos by views over a date range -- helps spot what is/isn't growing."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.query_analytics(
        ctx, resolved["account"], channel_id=params.channel_id,
        start_date=params.start_date, end_date=params.end_date,
        metrics="views,estimatedMinutesWatched,averageViewPercentage",
        dimensions="video", sort="-views", max_results=params.limit,
    )
    if not out.get("ok"):
        return _error(out)
    return _success(to_analytics_report(out), "Top videos loaded.")


@chat.function("get_traffic_sources", "Read where a channel's views come from (search, suggested, external, playlists, etc.) over a date range.",
               action_type="read", data_model=AnalyticsReport)
async def get_traffic_sources(ctx, params: GetTrafficSourcesParams) -> "ActionResult":
    """Read where a channel's views come from (search, suggested, external, playlists, etc.) over a date range."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.query_analytics(
        ctx, resolved["account"], channel_id=params.channel_id,
        start_date=params.start_date, end_date=params.end_date,
        metrics="views,estimatedMinutesWatched", dimensions="insightTrafficSourceType", sort="-views",
    )
    if not out.get("ok"):
        return _error(out)
    return _success(to_analytics_report(out), "Traffic sources loaded.")


# ------------------------------------------------------- channel management

@chat.function("list_channel_playlists", "List a channel's playlists (Channel Management tab).",
               action_type="read", data_model=PlaylistList)
async def list_channel_playlists(ctx, params: ListChannelPlaylistsParams) -> "ActionResult":
    """List a channel's playlists (Channel Management tab)."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.list_channel_playlists(ctx, resolved["account"], params.channel_id)
    if not out.get("ok"):
        return _error(out)
    rows = [to_playlist(raw) for raw in out["items"]]
    return _success(PlaylistList(items=rows), f"{len(rows)} playlist(s).")


@chat.function("list_playlist_items", "List the videos inside one playlist.",
               action_type="read", data_model=PlaylistItemList)
async def list_playlist_items(ctx, params: ListPlaylistItemsParams) -> "ActionResult":
    """List the videos inside one playlist."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.list_playlist_items(ctx, resolved["account"], params.playlist_id)
    if not out.get("ok"):
        return _error(out)
    rows = [to_playlist_item(raw) for raw in out["items"]]
    return _success(PlaylistItemList(items=rows), f"{len(rows)} item(s) in this playlist.")


@chat.function("list_comments", "List recent comments on one video, for moderation (Channel Management tab).",
               action_type="read", data_model=CommentList)
async def list_comments(ctx, params: ListCommentsParams) -> "ActionResult":
    """List recent comments on one video, for moderation (Channel Management tab)."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.list_comments(ctx, resolved["account"], params.video_id)
    if not out.get("ok"):
        return _error(out)
    rows = [to_comment(raw) for raw in out["items"]]
    return _success(CommentList(items=rows), f"{len(rows)} comment thread(s).")


@chat.function("reply_to_comment", "Post a reply to a comment thread, as the connected YouTube channel.",
               action_type="write", effects=["comment.reply.create"],
               event="youtube-studio-hub.comment.updated", data_model=Comment)
async def reply_to_comment(ctx, params: ReplyCommentParams) -> "ActionResult":
    """Post a reply to a comment thread, as the connected YouTube channel."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.reply_to_comment(ctx, resolved["account"], params.comment_thread_id, params.text)
    if not out.get("ok"):
        return _error(out)
    return _success(to_comment(out["item"]), "Reply posted.", ["yt_center"])


@chat.function("moderate_comment", "Set a comment's moderation status: published, held for review, or rejected.",
               action_type="write", effects=["comment.moderation.update"],
               event="youtube-studio-hub.comment.updated", data_model=SettingResult)
async def moderate_comment(ctx, params: ModerateCommentParams) -> "ActionResult":
    """Set a comment's moderation status: published, held for review, or rejected."""
    resolved = await _resolved(ctx, "")
    if not resolved.get("ok"):
        return _error(resolved)
    out = await yc.moderate_comment(ctx, resolved["account"], params.comment_id, params.status)
    if not out.get("ok"):
        return _error(out)
    return _success(SettingResult(id=params.comment_id, title="Comment", account="", enabled=True, action=params.status),
                     f"Comment moderation set to {params.status}.", ["yt_center"])


# --------------------------------------------------------------- content ideas

@chat.function("save_content_idea", "Save a content idea/SEO note for a channel -- title direction, target keyword, and why it might work.",
               action_type="write", effects=["idea.create"],
               event="youtube-studio-hub.idea.updated", data_model=Idea)
async def save_content_idea(ctx, params: SaveIdeaParams) -> "ActionResult":
    """Save a content idea/SEO note for a channel -- title direction, target keyword, and why it might work."""
    from models import Idea
    doc = await ctx.store.create(IDEAS, {
        "channel_id": params.channel_id, "title": params.title,
        "target_keyword": params.target_keyword, "rationale": params.rationale,
        "status": "idea",
    })
    idea = Idea(id=doc.id, channel_id=params.channel_id, title=params.title,
                target_keyword=params.target_keyword, rationale=params.rationale, status="idea")
    return _success(idea, "Idea saved.", ["yt_center"])


@chat.function("list_content_ideas", "List saved content ideas for a channel (Content Ideas tab).",
               action_type="read", data_model=IdeaList)
async def list_content_ideas(ctx, params: ListIdeasParams) -> "ActionResult":
    """List saved content ideas for a channel (Content Ideas tab)."""
    from models import Idea
    page = await ctx.store.query(IDEAS, where={"channel_id": params.channel_id}, limit=100)
    rows = [Idea(id=d.id, **{k: v for k, v in (d.data or {}).items() if k != "id"}) for d in page.data]
    return _success(IdeaList(items=rows), f"{len(rows)} idea(s) for this channel.")


@chat.function("generate_content_ideas", "Ask the AI to draft new content ideas for a channel, grounded in its recent top-performing videos and traffic sources -- helps answer 'what should I film next'.",
               action_type="write", effects=["idea.create"],
               event="youtube-studio-hub.idea.updated", data_model=IdeaList)
async def generate_content_ideas(ctx, params: GenerateContentIdeasParams) -> "ActionResult":
    """Ask the AI to draft new content ideas for a channel, grounded in its recent top-performing videos and traffic sources -- helps answer 'what should I film next'."""
    from models import Idea
    prompt = (
        f"You generate YouTube content ideas for the channel '{params.channel_title}'.\n"
        f"Extra context from the creator: {params.context or 'none provided'}\n\n"
        f"Produce exactly {params.count} concrete video ideas as a numbered list. "
        "For each: a specific title, one target search keyword/phrase, and one sentence on why "
        "it should perform (audience gap, trend, or search demand). Keep titles under 70 characters."
    )
    result = await ctx.ai.complete(prompt, model="claude-sonnet-4-6")
    text = result.text if hasattr(result, "text") else str(result)
    rows = []
    for line in text.splitlines():
        line = line.strip().lstrip("0123456789.-) ").strip()
        if not line:
            continue
        doc = await ctx.store.create(IDEAS, {
            "channel_id": params.channel_id, "title": line[:200],
            "target_keyword": "", "rationale": "AI-suggested", "status": "idea",
        })
        rows.append(Idea(id=doc.id, channel_id=params.channel_id, title=line[:200],
                          target_keyword="", rationale="AI-suggested", status="idea"))
        if len(rows) >= params.count:
            break
    return _success(IdeaList(items=rows), f"{len(rows)} idea(s) generated.", ["yt_center"])


@chat.function("update_idea_status", "Move a content idea to a new status: idea, planned, filmed, or done.",
               action_type="write", effects=["idea.update"],
               event="youtube-studio-hub.idea.updated", data_model=Idea)
async def update_idea_status(ctx, params: UpdateIdeaStatusParams) -> "ActionResult":
    """Move a content idea to a new status: idea, planned, filmed, or done."""
    from models import Idea
    doc = await ctx.store.get(IDEAS, params.idea_id)
    if not doc:
        return ActionResult.error("That idea no longer exists.", retryable=False, code="YOUTUBE_IDEA_MISSING")
    await ctx.store.update(IDEAS, params.idea_id, {"status": params.status})
    data = dict(doc.data or {})
    data["status"] = params.status
    return _success(Idea(id=params.idea_id, **{k: v for k, v in data.items() if k != "id"}), "Idea updated.", ["yt_center"])


@chat.function("delete_idea", "Permanently delete a saved content idea.",
               action_type="write", effects=["idea.delete"],
               event="youtube-studio-hub.idea.updated", data_model=SettingResult)
async def delete_idea(ctx, params: DeleteIdeaParams) -> "ActionResult":
    """Permanently delete a saved content idea."""
    await ctx.store.delete(IDEAS, params.idea_id)
    return _success(SettingResult(id=params.idea_id, title="Idea", account="", enabled=False, action="deleted"),
                     "Idea deleted.", ["yt_center"])
