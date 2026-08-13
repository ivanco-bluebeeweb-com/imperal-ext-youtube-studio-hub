"""YouTube Studio Hub declaration and unified OAuth configuration.

Mirrors the same OAuth pattern as Google Drive Connector / Google Analytics
Bluebee: ext.oauth() declares the provider once, the platform's unified
gateway route runs the OAuth dance and writes one account record per
connected Google account to `collection`. Client creds are app-scoped
secrets set once in the Developer Portal -- never shown to end users.

Scopes requested (verified against Google's own docs before writing this,
see PREPARATION.md section 12 for the exact URLs read):
  - youtube.readonly        -- list channels/videos/playlists (read)
  - youtube.force-ssl       -- write access: update video metadata,
                                thumbnails, playlists, reply to/moderate
                                comments (full read+write per the YouTube
                                Data API v3 docs; there is no separate
                                "metadata-only write" scope)
  - yt-analytics.readonly   -- YouTube Analytics API reports.query
  - yt-analytics-monetary.readonly -- revenue metrics; the reports.query
                                endpoint itself doesn't error without it,
                                individual monetary rows are simply absent
                                from the response if this scope wasn't
                                granted -- panels must not assume this data
                                exists (see ux-spec.md section 6).
"""

from imperal_sdk import ChatExtension, Extension

ext = Extension(
    "youtube-studio-hub",
    version="0.1.0",
    display_name="YouTube Studio Hub",
    description=(
        "Connect one or more Google accounts and manage every YouTube "
        "channel they own from one place: content metadata, analytics, "
        "channel management (playlists/comments), and content ideas. "
        "Does not edit, trim, or re-encode video files -- that stays in "
        "YouTube Studio."
    ),
    icon="icon.svg",
    capabilities=["youtube:read", "youtube:write", "youtube:settings"],
    actions_explicit=True,
)

chat = ChatExtension(
    ext,
    tool_name="youtube_studio",
    description=(
        "YouTube Studio Hub -- connect Google accounts, list channels and "
        "videos, read channel/video analytics, edit video metadata "
        "(title/description/tags/thumbnail/visibility), manage playlists "
        "and comments, and generate content ideas from query signals."
    ),
)

ext.oauth(
    "google",
    collection="youtube_accounts",
    scopes=[
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
        "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
    ],
)

# Developer-owned OAuth app credentials, set once in the Developer Portal.
ext.secret(
    "google_client_id",
    "Google OAuth client ID for YouTube Studio Hub.",
    required=True,
    scope="app",
)(lambda: None)
ext.secret(
    "google_client_secret",
    "Google OAuth client secret for YouTube Studio Hub.",
    required=True,
    scope="app",
)(lambda: None)


@ext.health_check
async def health_check(ctx) -> dict:
    """Fast configuration health; no third-party call, no secrets exposed."""
    client_id = await ctx.secrets.get("google_client_id")
    client_secret = await ctx.secrets.get("google_client_secret")
    configured = bool(client_id) and bool(client_secret) and client_id != client_secret
    return {
        "ok": configured,
        "detail": "Google OAuth credentials configured." if configured
        else "Google OAuth client ID/secret are missing or identical in Secrets.",
    }
