"""YouTube Studio Hub panels — sidebar (left) + center detail view.

Sidebar layout, top to bottom, per approved spec (design/ux-spec.md §1):
  1. "Connect Google Account" CTA button -> opens ui.Dialog with the
     Google OAuth link.
  2. ui.Divider
  3. Google account selector (ui.Select, switches the active account)
     plus an "Add another Google account" button.
  4. ui.Divider(label="Channels")
  5. Clickable list of channels (avatar + title) belonging to the active
     account only -- the ONLY channel picker; there is no second,
     redundant channel ui.Select next to it. Clicking one loads the
     channel detail in the center panel.
  6. ui.Divider
  7. "App Settings" button -- last item in the sidebar.

Center layout: empty state until a channel is selected. Once selected:
  channel title + link, then a tab bar (My Content (N) [default] / Analytics
  / Channel Management / Content Ideas). Clicking a video in My Content
  swaps the center to that video's detail view.
"""

from __future__ import annotations

from imperal_sdk import ui

import accounts
import youtube_client as yc
from app import ext
from converters import to_channel, to_comment, to_playlist, to_video

APP_ID = "youtube-studio-hub"
REDIRECT_URI = f"https://panel.imperal.io/v1/ext/{APP_ID}/oauth/google/callback"


def _email(doc) -> str:
    return accounts.account_email(doc)


def _label(doc) -> str:
    return accounts.account_label(doc)


async def _connect_dialog_content(ctx) -> ui.Stack:
    client_id = await ctx.secrets.get("google_client_id")
    client_secret = await ctx.secrets.get("google_client_secret")
    if not client_id or not client_secret:
        return ui.Stack(children=[
            ui.Text("Google OAuth is not configured for this app yet.", variant="body"),
            ui.Text("An admin must set google_client_id / google_client_secret in Secrets before an account can connect.", variant="caption"),
        ])

    docs = await accounts.all_accounts(ctx)
    connected_rows = [
        ui.Stack(direction="h", justify="between", children=[
            ui.Text(_label(doc), variant="body"),
            ui.Text("Connected", variant="caption"),
        ])
        for doc in docs if not accounts.identity_missing(doc)
    ]

    try:
        url = await ctx.oauth_authorize_url("google")
    except Exception:
        url = ""
    if not url:
        return ui.Stack(children=[
            ui.Text("Could not build a Google authorization link right now. Try again in a moment.", variant="body"),
        ])

    connect_btn = ui.Button(
        "Add another Google account" if connected_rows else "Continue with Google",
        icon="ExternalLink", variant="primary" if not connected_rows else "secondary",
        full_width=True, on_click=ui.Open(url),
    )

    if connected_rows:
        return ui.Stack(children=[
            ui.Text("Connected Google accounts", variant="caption"),
            *connected_rows,
            ui.Divider(),
            connect_btn,
        ])

    return ui.Stack(children=[
        ui.Text("YouTube Studio Hub can read and manage every channel this Google account owns: content metadata, analytics, playlists, and comments. It never edits or trims the video file itself, and never publishes new uploads.", variant="body"),
        connect_btn,
    ])


async def _channel_options(ctx) -> list[dict]:
    """All channels across all connected accounts, cached on each account
    doc's channel_ids/channel_titles so the sidebar list and Select don't
    need a live API call on every render."""
    options = []
    for doc in await accounts.all_accounts(ctx):
        data = doc.data or {}
        ids = data.get("channel_ids") or []
        titles = data.get("channel_titles") or {}
        thumbs = data.get("channel_thumbnails") or {}
        for cid in ids:
            options.append({
                "channel_id": cid,
                "title": titles.get(cid, cid),
                "thumbnail": thumbs.get(cid, ""),
                "account_email": _email(doc),
            })
    return options


async def _refresh_channel_cache(ctx, doc) -> list[dict]:
    """Live-fetch this account's channels and cache id/title/thumbnail on
    the account doc, so the sidebar can render from cache next time."""
    out = await yc.list_my_channels(ctx, doc)
    if not out.get("ok"):
        return []
    channels = [to_channel(raw, account_email=_email(doc)) for raw in out["items"]]
    ids = [c.channel_id for c in channels]
    titles = {c.channel_id: c.title for c in channels}
    thumbs = {c.channel_id: c.thumbnail_url for c in channels}
    from accounts import ACCOUNTS
    await ctx.store.update(ACCOUNTS, doc.id, {
        "channel_ids": ids, "channel_titles": titles, "channel_thumbnails": thumbs,
    })
    return [{"channel_id": c.channel_id, "title": c.title, "thumbnail": c.thumbnail_url,
             "account_email": _email(doc)} for c in channels]


def _account_options(docs) -> list[dict]:
    return [{"label": _label(doc), "value": _email(doc)} for doc in docs if not accounts.identity_missing(doc)]


@ext.panel("yt_nav", slot="left", title="YouTube Studio", icon="Youtube",
           default_width=280, min_width=220, max_width=400,
           refresh="on_event:youtube-studio-hub.account.updated")
async def yt_nav(ctx, active_channel: str = "", **kwargs):
    docs = await accounts.all_accounts(ctx)

    connect_btn = ui.Button(
        "Connect Google Account", icon="Plus", variant="primary", full_width=True,
        on_click=ui.Call("__panel__yt_connect_dialog"),
    )

    if not docs:
        return ui.Stack(children=[
            connect_btn,
            ui.Divider(),
            ui.Empty(message="No Google account connected yet.", icon="Youtube"),
        ])

    active = await accounts.resolve_account(ctx)
    active_doc = active.get("account") if active.get("ok") else docs[0]
    active_email = _email(active_doc)

    account_select = ui.Select(
        options=_account_options(docs), value=active_email,
        placeholder="Select a Google account…", param_name="account",
        on_change=ui.Call("switch_account", account="{{value}}"),
    )
    add_account_btn = ui.Button(
        "Add another Google account", icon="Plus", variant="ghost", full_width=True,
        on_click=ui.Call("__panel__yt_connect_dialog"),
    )

    all_options = await _channel_options(ctx)
    active_has_cache = any(o["account_email"].lower() == active_email.lower() for o in all_options)
    if not all_options or not active_has_cache:
        # Refresh live whenever the cache is totally empty, OR the ACTIVE
        # account specifically has no cached channels yet (e.g. it was just
        # connected while another account's cache was already populated --
        # only checking "is the whole list empty" would leave this account
        # permanently blank until some unrelated full-cache-miss happened).
        for doc in docs:
            if _email(doc).lower() == active_email.lower() or not all_options:
                all_options = [o for o in all_options if o["account_email"].lower() != _email(doc).lower()]
                all_options.extend(await _refresh_channel_cache(ctx, doc))
    options = [o for o in all_options if o["account_email"].lower() == active_email.lower()]

    channel_items = [
        ui.ListItem(
            id=o["channel_id"], title=o["title"],
            avatar=ui.Avatar(fallback=(o["title"][:1] or "?"), src=o["thumbnail"]),
            selected=(o["channel_id"] == active_channel),
            on_click=ui.Call("__panel__yt_center2", channel_id=o["channel_id"]),
        )
        for o in options
    ]

    return ui.Stack(children=[
        connect_btn,
        ui.Divider(),
        ui.Text("Google account", variant="caption"),
        account_select,
        add_account_btn,
        ui.Divider(label="Channels"),
        ui.List(items=channel_items) if channel_items else ui.Empty(message="No channels on this account yet.", icon="Youtube"),
        ui.Divider(),
        ui.Button("App Settings", icon="Settings", variant="secondary", full_width=True,
                  on_click=ui.Call("__panel__yt_settings2")),
    ])


@ext.panel("yt_connect_dialog", slot="center", title="Connect Google Account",
           center_overlay=True)
async def yt_connect_dialog(ctx, **kwargs):
    content = await _connect_dialog_content(ctx)
    return ui.Dialog(title="Connect Google Account", content=content, confirm_label="", cancel_label="Close")


@ext.panel("yt_settings2", slot="center", title="App Settings", center_overlay=True)
async def yt_settings(ctx, **kwargs):
    docs = await accounts.all_accounts(ctx)
    setting = await accounts.app_setting(ctx)
    rows = []
    for doc in docs:
        rows.append(ui.Stack(direction="h", justify="between", children=[
            ui.Stack(children=[
                ui.Text(_label(doc), variant="body"),
                ui.Text(_email(doc), variant="caption"),
            ]),
            ui.Button("Disconnect", icon="Unplug", variant="secondary", size="sm",
                      on_click=ui.Call("disconnect_account", account_id=doc.id)),
        ]))
    ai_ideas_default = bool(setting.get("ai_ideas_enabled", True))
    account_rows = rows if rows else [ui.Empty(message="No accounts connected.")]
    return ui.Dialog(title="App Settings", confirm_label="", cancel_label="Close", content=ui.Stack(children=[
        ui.Text("Connected Google accounts", variant="caption"),
        *account_rows,
        ui.Divider(),
        ui.Toggle(label="Enable AI-generated content ideas", value=ai_ideas_default,
                  param_name="ai_ideas_enabled",
                  on_change=ui.Call("save_app_setting")),
    ]))


# --------------------------------------------------------------- center


def _tab_bar(channel_id: str, active_tab: str, video_count: int) -> ui.Stack:
    def call(tab):
        return ui.Call("__panel__yt_center2", channel_id=channel_id, tab=tab)

    def btn(label, key):
        return ui.Button(label, variant="secondary" if active_tab == key else "ghost",
                          size="sm", on_click=call(key))

    return ui.Stack(direction="h", gap=2, wrap=True, children=[
        btn(f"My Content ({video_count})", "content"),
        btn("Analytics", "analytics"),
        btn("Channel Management", "management"),
        btn("Content Ideas", "ideas"),
    ])


async def _channel_header(ctx, channel_id: str):
    resolved = await accounts.account_for_channel(ctx, channel_id)
    if not resolved.get("ok"):
        return None, resolved
    out = await yc.get_channel(ctx, resolved["account"], channel_id)
    if not out.get("ok"):
        return None, out
    channel = to_channel(out["item"])
    return channel, resolved


async def _content_tab(ctx, account_doc, channel_id: str):
    out = await yc.list_channel_videos(ctx, account_doc, channel_id, limit=25, page_token="")
    if not out.get("ok"):
        return ui.Alert(str(out.get("error") or "Could not load videos."), title="Content"), 0
    videos = [to_video(raw, channel_id=channel_id) for raw in out["items"]]
    items = [
        ui.ListItem(
            id=v.video_id, title=v.title,
            subtitle=f"{v.view_count:,} views · {v.published_at[:10]}",
            avatar=ui.Avatar(fallback="▶", src=v.thumbnail_url),
            badge=ui.Badge(label=v.visibility, color="gray" if v.visibility == "private" else "green"),
            on_click=ui.Call("__panel__yt_center2", channel_id=channel_id, tab="content", video_id=v.video_id),
        )
        for v in videos
    ]
    body = ui.List(items=items) if items else ui.Empty(message="No videos on this channel yet.", icon="Video")
    return body, len(videos)


async def _analytics_tab(ctx, account_doc, channel_id: str, end_date: str, start_date: str):
    out = await yc.query_analytics(
        ctx, account_doc, channel_id=channel_id, start_date=start_date, end_date=end_date,
        metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost,averageViewDuration",
    )
    if not out.get("ok"):
        return ui.Alert(str(out.get("error") or "Could not load analytics."), title="Analytics")
    from converters import to_analytics_report
    report = to_analytics_report(out)
    totals = {}
    for row in report.rows:
        for k, v in row.values.items():
            totals[k] = totals.get(k, 0) + v
    stats = [ui.Stat(label=k.replace("estimatedMinutesWatched", "Minutes watched")
                     .replace("subscribersGained", "Subs gained")
                     .replace("subscribersLost", "Subs lost")
                     .replace("averageViewDuration", "Avg. view duration (s)")
                     .replace("views", "Views"), value=round(v, 1))
             for k, v in totals.items()]
    top_out = await yc.query_analytics(
        ctx, account_doc, channel_id=channel_id, start_date=start_date, end_date=end_date,
        metrics="views,estimatedMinutesWatched,averageViewPercentage",
        dimensions="video", sort="-views", max_results=10,
    )
    top_block = ui.Empty(message="No top-video data for this range.")
    if top_out.get("ok"):
        top_report = to_analytics_report(top_out)
        top_items = [
            ui.ListItem(id=r.values.get("video", ""), title=r.dimensions.get("video", "video"),
                        subtitle=f"{int(r.values.get('views', 0)):,} views")
            for r in top_report.rows[:10]
        ]
        if top_items:
            top_block = ui.List(items=top_items)
    return ui.Stack(children=[
        ui.Text(f"{start_date} → {end_date}", variant="caption"),
        ui.Stats(children=stats) if stats else ui.Empty(message="No analytics data for this range."),
        ui.Divider(label="Top videos by views"),
        top_block,
    ])


async def _management_tab(ctx, account_doc, channel_id: str):
    pl_out = await yc.list_channel_playlists(ctx, account_doc, channel_id)
    playlists = [to_playlist(raw) for raw in pl_out.get("items", [])] if pl_out.get("ok") else []
    playlist_items = [
        ui.ListItem(id=p.playlist_id, title=p.title, subtitle=f"{p.item_count} video(s)",
                    on_click=ui.Call("__panel__yt_center2", channel_id=channel_id, tab="management", playlist_id=p.playlist_id))
        for p in playlists
    ]
    return ui.Stack(children=[
        ui.Text("Playlists", variant="caption"),
        ui.List(items=playlist_items) if playlist_items else ui.Empty(message="No playlists on this channel."),
        ui.Divider(),
        ui.Text("Open a video from My Content to moderate its comments.", variant="caption"),
    ])


async def _ideas_tab(ctx, channel_id: str, channel_title: str):
    page = await ctx.store.query("youtube_content_ideas", where={"channel_id": channel_id}, limit=100)
    ideas = list(page.data)
    items = [
        ui.ListItem(
            id=d.id, title=str((d.data or {}).get("title") or ""),
            subtitle=str((d.data or {}).get("target_keyword") or ""),
            badge=ui.Badge(label=str((d.data or {}).get("status") or "idea"), color="blue"),
            actions=[{"label": "Delete", "icon": "Trash2", "on_click": ui.Call("delete_idea", idea_id=d.id),
                      "confirm": "Delete this content idea?"}],
        )
        for d in ideas
    ]
    return ui.Stack(children=[
        ui.Button("Ask AI for new ideas", icon="Sparkles", variant="primary",
                  on_click=ui.Call("generate_content_ideas", channel_id=channel_id, channel_title=channel_title)),
        ui.Divider(),
        ui.List(items=items) if items else ui.Empty(message="No saved ideas yet — ask the AI or add your own from chat.", icon="Lightbulb"),
    ])


@ext.panel("yt_center2", slot="center", title="YouTube Studio Hub", center_overlay=True,
           refresh="on_event:youtube-studio-hub.video.updated,youtube-studio-hub.idea.updated,youtube-studio-hub.comment.updated")
async def yt_center(ctx, channel_id: str = "", tab: str = "content", video_id: str = "",
                     start_date: str = "", end_date: str = "", **kwargs):
    if not channel_id:
        return ui.Empty(message="Select a channel from the sidebar to get started.", icon="Youtube")

    channel, resolved = await _channel_header(ctx, channel_id)
    if channel is None:
        return ui.Alert(str((resolved or {}).get("error") or "Could not load this channel."), title="Channel")
    account_doc = resolved["account"]

    if video_id:
        return await _video_detail(ctx, account_doc, channel_id, video_id, channel.title)

    header = ui.Stack(children=[
        ui.Header(channel.title, level=2),
        ui.Link(text=channel.channel_url, href=channel.channel_url),
    ])

    from datetime import date, timedelta
    end_date = end_date or date.today().isoformat()
    start_date = start_date or (date.today() - timedelta(days=28)).isoformat()

    content_body, video_count = await _content_tab(ctx, account_doc, channel_id)
    tabs = _tab_bar(channel_id, tab, video_count)

    if tab == "analytics":
        body = await _analytics_tab(ctx, account_doc, channel_id, end_date, start_date)
    elif tab == "management":
        body = await _management_tab(ctx, account_doc, channel_id)
    elif tab == "ideas":
        body = await _ideas_tab(ctx, channel_id, channel.title)
    else:
        body = content_body

    return ui.Stack(children=[header, tabs, ui.Divider(), body])


async def _video_detail(ctx, account_doc, channel_id: str, video_id: str, channel_title: str):
    out = await yc.get_video(ctx, account_doc, video_id)
    if not out.get("ok"):
        return ui.Alert(str(out.get("error") or "Could not load this video."), title="Video")
    video = to_video(out["item"])

    from datetime import date, timedelta
    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=28)).isoformat()
    analytics_out = await yc.query_analytics(
        ctx, account_doc, channel_id=channel_id, start_date=start_date, end_date=end_date,
        metrics="views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares",
        filters=f"video=={video_id}",
    )
    stats = []
    if analytics_out.get("ok"):
        from converters import to_analytics_report
        report = to_analytics_report(analytics_out)
        totals = {}
        for row in report.rows:
            for k, v in row.values.items():
                totals[k] = totals.get(k, 0) + v
        stats = [ui.Stat(label=k, value=round(v, 1)) for k, v in totals.items()]

    comments_out = await yc.list_comments(ctx, account_doc, video_id)
    comments = [to_comment(raw) for raw in comments_out.get("items", [])] if comments_out.get("ok") else []
    comment_items = [
        ui.ListItem(id=c.comment_id, title=c.author, subtitle=c.text,
                    meta=f"{c.like_count} like(s)")
        for c in comments
    ]

    return ui.Stack(children=[
        ui.Button("← Back to channel", icon="ArrowLeft", variant="ghost", size="sm",
                  on_click=ui.Call("__panel__yt_center2", channel_id=channel_id, tab="content")),
        ui.Header(video.title, level=2),
        ui.Link(text=f"https://youtu.be/{video_id}", href=f"https://youtu.be/{video_id}"),
        ui.Text(video.description[:400], variant="caption"),
        ui.Stats(children=stats) if stats else ui.Empty(message="No analytics for this video yet."),
        ui.Divider(label="Edit metadata"),
        ui.Form(
            action="update_video_metadata", submit_label="Save changes",
            defaults={"video_id": video_id},
            children=[
                ui.Text("Title", variant="label"),
                ui.Input(placeholder="Title", value=video.title, param_name="title"),
                ui.Text("Description", variant="label"),
                ui.TextArea(placeholder="Description", value=video.description, param_name="description"),
                ui.Text("Tags (comma separated)", variant="label"),
                ui.Input(placeholder="tag1, tag2, tag3", value=", ".join(video.tags), param_name="tags_csv"),
            ],
        ),
        ui.Divider(label=f"Comments ({len(comments)})"),
        ui.List(items=comment_items) if comment_items else ui.Empty(message="No comments yet."),
    ])
