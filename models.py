"""Typed parameters and SDL entities for YouTube Studio Hub.

Field names here are the single source of truth consumed by converters.py,
youtube_client.py, and handlers.py -- keep it in sync if any of those change.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from imperal_sdk import sdl


class NoParams(BaseModel):
    pass


class AccountScoped(BaseModel):
    account: str = Field(
        "", description="Connected Google account email. Omit when only one account is connected."
    )


class DisconnectAccountParams(BaseModel):
    account_id: str = Field(..., description="Stable id of the connected Google account to disconnect")


class ListAccountsParams(BaseModel):
    refresh: bool = Field(False, description="Verify each account against the YouTube Data API")


class ListChannelsParams(AccountScoped):
    pass


class ChannelScoped(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id, e.g. UCxxxxxxxx")


class ListChannelVideosParams(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id")
    limit: int = Field(25, ge=1, le=50, description="Maximum videos to return")
    page_token: str = Field("", description="Continuation token from the previous page")


class GetVideoParams(BaseModel):
    video_id: str = Field(..., description="YouTube video id")


class UpdateVideoMetadataParams(BaseModel):
    video_id: str = Field(..., description="YouTube video id")
    title: str = Field("", description="New title; empty keeps the current title")
    description: str = Field("", description="New description; empty keeps the current description")
    tags: list[str] = Field(default_factory=list, description="Replacement tag list; empty list keeps current tags")
    category_id: str = Field("", description="YouTube video category id; empty keeps the current category")
    visibility: str = Field("", description="private, unlisted, or public; empty keeps current visibility")


class SetVideoThumbnailParams(BaseModel):
    video_id: str = Field(..., description="YouTube video id")
    image_url: str = Field(..., description="Publicly reachable https:// image URL to set as the thumbnail")


class GetChannelAnalyticsParams(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id")
    start_date: str = Field(..., description="ISO date, e.g. 2026-07-01")
    end_date: str = Field(..., description="ISO date, e.g. 2026-08-01")


class GetVideoAnalyticsParams(BaseModel):
    video_id: str = Field(..., description="YouTube video id")
    channel_id: str = Field(..., description="Owning channel id (Analytics API requires it for the filter)")
    start_date: str = Field(..., description="ISO date, e.g. 2026-07-01")
    end_date: str = Field(..., description="ISO date, e.g. 2026-08-01")


class ListChannelPlaylistsParams(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id")


class PlaylistScoped(BaseModel):
    playlist_id: str = Field(..., description="YouTube playlist id")


class ListPlaylistItemsParams(BaseModel):
    playlist_id: str = Field(..., description="YouTube playlist id")


class ListCommentsParams(BaseModel):
    video_id: str = Field(..., description="YouTube video id")


class ReplyCommentParams(BaseModel):
    comment_thread_id: str = Field(..., description="Top-level comment/thread id to reply under")
    text: str = Field(..., description="Reply text")


class SaveIdeaParams(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id this idea is for")
    title: str = Field(..., description="Idea/working title")
    target_keyword: str = Field("", description="Target search keyword/phrase, if any")
    rationale: str = Field("", description="Why this idea might work")


class ListIdeasParams(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id")


class SaveAppSettingParams(BaseModel):
    ai_ideas_enabled: bool = Field(True, description="Whether Content Ideas may use AI generation")


class SwitchAccountParams(BaseModel):
    account: str = Field("", description="Connected Google account email to make active")


class GetTopVideosParams(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id")
    start_date: str = Field(..., description="ISO date, e.g. 2026-07-01")
    end_date: str = Field(..., description="ISO date, e.g. 2026-08-01")
    limit: int = Field(10, ge=1, le=50, description="Maximum videos to return")


class GetTrafficSourcesParams(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id")
    start_date: str = Field(..., description="ISO date, e.g. 2026-07-01")
    end_date: str = Field(..., description="ISO date, e.g. 2026-08-01")


class ModerateCommentParams(BaseModel):
    comment_id: str = Field(..., description="YouTube comment id")
    status: str = Field(..., description="published, heldForReview, or rejected")


class GenerateContentIdeasParams(BaseModel):
    channel_id: str = Field(..., description="YouTube channel id")
    channel_title: str = Field(..., description="Channel title, used to ground the AI prompt")
    context: str = Field("", description="Extra creator context/direction for idea generation")
    count: int = Field(5, ge=1, le=15, description="How many ideas to generate")


class UpdateIdeaStatusParams(BaseModel):
    idea_id: str = Field(..., description="Content idea id")
    status: str = Field(..., description="idea, planned, filmed, or done")


class DeleteIdeaParams(BaseModel):
    idea_id: str = Field(..., description="Content idea id to permanently delete")


# ------------------------------------------------------------------ entities

class Account(sdl.Entity):
    email: str = ""
    active: bool = False
    state: str = "connected"


class AccountList(sdl.EntityList[Account]):
    pass


class SettingResult(sdl.Entity):
    account: str = ""
    enabled: bool = False
    action: str = ""


class Channel(sdl.Entity):
    channel_id: str = ""
    thumbnail_url: str = ""
    subscriber_count: int = 0
    video_count: int = 0
    view_count: int = 0
    channel_url: str = ""
    account_email: str = ""


class ChannelList(sdl.EntityList[Channel]):
    pass


class Video(sdl.Entity):
    video_id: str = ""
    channel_id: str = ""
    thumbnail_url: str = ""
    published_at: str = ""
    duration: str = ""
    visibility: str = ""
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    tags: list[str] = Field(default_factory=list)
    category_id: str = ""
    video_url: str = ""


class VideoList(sdl.EntityList[Video]):
    next_page_token: str = ""


class Playlist(sdl.Entity):
    playlist_id: str = ""
    channel_id: str = ""
    item_count: int = 0
    visibility: str = ""
    playlist_url: str = ""


class PlaylistList(sdl.EntityList[Playlist]):
    pass


class PlaylistItem(sdl.Entity):
    playlist_item_id: str = ""
    playlist_id: str = ""
    video_id: str = ""
    position: int = 0


class PlaylistItemList(sdl.EntityList[PlaylistItem]):
    pass


class Comment(sdl.Entity):
    comment_id: str = ""
    video_id: str = ""
    author: str = ""
    author_channel_id: str = ""
    text: str = ""
    like_count: int = 0
    published_at: str = ""
    moderation_status: str = "published"
    reply_count: int = 0


class CommentList(sdl.EntityList[Comment]):
    pass


class AnalyticsRow(BaseModel):
    dimension_values: list[str] = Field(default_factory=list)
    metric_values: list[float] = Field(default_factory=list)


class AnalyticsReport(sdl.Entity):
    channel_id: str = ""
    video_id: str = ""
    start_date: str = ""
    end_date: str = ""
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    rows: list[AnalyticsRow] = Field(default_factory=list)
    monetary_data_available: bool = False


class Idea(sdl.Entity):
    channel_id: str = ""
    target_keyword: str = ""
    rationale: str = ""
    status: str = "idea"


class IdeaList(sdl.EntityList[Idea]):
    pass
