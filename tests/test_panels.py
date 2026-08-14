"""Regression coverage for the YouTube Studio Hub sidebar/center panels:

- The channel picker must be the clickable list ONLY -- no second,
  redundant ui.Select duplicating it.
- "App Settings" must be the LAST item rendered in the sidebar.
- Clicking a channel must actually load its content: an account that is
  active but has no cached channel_ids yet (e.g. just connected while
  another account already had a populated cache) must still get its
  channels refreshed live, not stay permanently blank.
- The yt_center panel MUST be declared with center_overlay=True. Every
  channel-click flows through ui.Call("__panel__yt_center", channel_id=...)
  fired from the LEFT sidebar; the frontend only renders a slot="center"
  panel fetched this way when center_overlay is set (see
  imperal_sdk.types.contributions.PANEL_SLOT_RENDERING_STATUS -- a plain
  slot="center" panel without the flag has no render path for on-demand
  __panel__ calls). Without it, the server-side call succeeds (data loads)
  but nothing ever appears on screen -- exactly the "loads then nothing"
  bug this test guards against.
"""
import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import panels
from app import ext


def _render(node) -> str:
    return str(node)


@pytest.mark.asyncio
async def test_sidebar_has_no_duplicate_channel_selector(ctx, account):
    rendered = _render(await panels.yt_nav(ctx))
    # The clickable channel list must be present...
    assert "UCabc123" in rendered
    # ...but there must be no second ui.Select rendering the same channels
    # (only the account ui.Select should exist).
    assert rendered.count("UINode(type='Select'") == 1


@pytest.mark.asyncio
async def test_app_settings_is_the_last_sidebar_item(ctx, account):
    rendered = _render(await panels.yt_nav(ctx))
    settings_pos = rendered.rfind("App Settings")
    assert settings_pos != -1
    # Nothing that belongs to a later logical block (channel list divider or
    # channel list itself) should appear AFTER the Settings button.
    assert rendered.rfind("UCabc123") < settings_pos
    assert rendered.rfind("Channels") < settings_pos or rendered.find("Channels") < settings_pos


@pytest.mark.asyncio
async def test_active_account_without_cached_channels_is_refreshed_live(ctx, account, monkeypatch):
    """Regression: previously the live refresh only ran when the WHOLE
    cache (across every account) was empty. A second, freshly-connected
    active account with its own empty channel_ids stayed blank forever
    because some OTHER account's cache made the global list non-empty."""
    other = await ctx.store.create("youtube_accounts", {
        "email": "other@example.com", "display_name": "Other", "is_active": False,
        "access_token": "tok", "refresh_token": "r", "token_expires_at": "2099-01-01T00:00:00+00:00",
        "channel_ids": ["UCother"], "channel_titles": {"UCother": "Other channel"},
    })
    await ctx.store.update("youtube_accounts", account.id, {"is_active": True, "channel_ids": []})

    async def fake_list_my_channels(ctx_, doc):
        return {"ok": True, "items": [{
            "id": "UCnew123",
            "snippet": {"title": "Freshly Connected Channel", "description": "",
                        "thumbnails": {"high": {"url": "https://img/new.jpg"}}},
            "statistics": {"subscriberCount": "5", "videoCount": "1", "viewCount": "10"},
        }]}

    monkeypatch.setattr(panels.yc, "list_my_channels", fake_list_my_channels)
    rendered = _render(await panels.yt_nav(ctx))
    assert "Freshly Connected Channel" in rendered
    assert "Other channel" not in rendered  # scoped to the ACTIVE account only


def test_yt_center_panel_is_declared_as_center_overlay():
    """Regression: clicking a channel in the sidebar calls
    ui.Call("__panel__yt_center", channel_id=...). The SDK only gives a
    slot="center" panel a render path for that on-demand __panel__ call
    when center_overlay=True is set (imperal_sdk.types.contributions ---
    PANEL_SLOT_RENDERING_STATUS["center"] == "center-overlay", gated by the
    frontend's isCenterOverlay flag). Without the flag the server call
    still succeeds and returns real data, but nothing ever renders on
    screen -- channel click "loads, then nothing" with no error shown.
    Every other panel in this codebase that is opened the same way
    (yt_connect_dialog, yt_settings) already carries the flag; yt_center
    must too."""
    panel_meta = ext.panels.get("yt_center")
    assert panel_meta is not None, "yt_center panel is not registered"
    assert panel_meta.get("slot") == "center"
    assert panel_meta.get("center_overlay") is True, (
        "yt_center must be declared with center_overlay=True or channel "
        "clicks silently render nothing"
    )
