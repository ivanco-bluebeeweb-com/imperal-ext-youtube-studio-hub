"""PST (Plausible Scenario Testing) — 2026-08-19.

All 25 @chat.function tools already had direct-call coverage in
test_smoke.py/test_panels.py (confirmed by a systematic name-grep), so this
pass instead builds plausible multi-step scenarios around the one area
that surface-level "is it called once" coverage cannot catch: what
disconnect_account actually cleans up.

Real bug found and fixed by this pass: disconnect_account's cleanup of
youtube_content_ideas keyed off `account_email`, but idea documents never
carry that field (only channel_id/title/target_keyword/rationale/status) --
so the cleanup query always matched zero rows and disconnecting an account
never removed its saved ideas, silently breaking the function's own
documented promise. Fixed in accounts.py to join through the account's
cached channel_ids instead.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import accounts
import handlers as h
from models import (
    DisconnectAccountParams, GenerateContentIdeasParams, ListIdeasParams,
    SaveIdeaParams,
)


async def test_disconnect_account_deletes_its_own_content_ideas(ctx, account):
    """Plausible real-world flow: creator connects, saves ideas for their
    channel, then disconnects -- the promised cleanup must actually happen."""
    await h.save_content_idea(ctx, SaveIdeaParams(
        channel_id="UCabc123", title="Idea one", target_keyword="kw", rationale="r"))
    await h.save_content_idea(ctx, SaveIdeaParams(
        channel_id="UCabc123", title="Idea two", target_keyword="kw2", rationale="r2"))

    out = await h.disconnect_account(ctx, DisconnectAccountParams(account_id=account.id))
    assert out.status == "success"

    remaining = await h.list_content_ideas(ctx, ListIdeasParams(channel_id="UCabc123"))
    assert remaining.data.items == [], (
        "disconnect_account must delete ideas for its own channels -- "
        "found leftover ideas after disconnect, the exact bug this test guards against")


async def test_disconnect_account_does_not_touch_other_accounts_ideas(ctx, account):
    """Multi-account household/agency scenario: disconnecting account A must
    never delete ideas that belong to account B's channel."""
    other = await ctx.store.create("youtube_accounts", {
        "email": "other@example.com", "display_name": "Other Creator",
        "access_token": "tok", "refresh_token": "rtok",
        "token_expires_at": "2099-01-01T00:00:00+00:00",
        "channel_ids": ["UCother999"],
    })
    await h.save_content_idea(ctx, SaveIdeaParams(
        channel_id="UCabc123", title="Mine", target_keyword="kw", rationale="r"))
    await h.save_content_idea(ctx, SaveIdeaParams(
        channel_id="UCother999", title="Theirs", target_keyword="kw", rationale="r"))

    out = await h.disconnect_account(ctx, DisconnectAccountParams(account_id=account.id))
    assert out.status == "success"

    mine = await h.list_content_ideas(ctx, ListIdeasParams(channel_id="UCabc123"))
    theirs = await h.list_content_ideas(ctx, ListIdeasParams(channel_id="UCother999"))
    assert mine.data.items == []
    assert len(theirs.data.items) == 1 and theirs.data.items[0].title == "Theirs"


async def test_disconnect_account_with_no_cached_channel_ids_still_removes_account(ctx):
    """Edge case: an account row saved before channel_ids was ever cached
    (or a channel-less/never-refreshed account) must still disconnect
    cleanly instead of erroring on a missing/empty list."""
    bare = await ctx.store.create("youtube_accounts", {
        "email": "bare@example.com", "display_name": "Bare",
        "access_token": "tok", "refresh_token": "rtok",
        "token_expires_at": "2099-01-01T00:00:00+00:00",
    })
    out = await h.disconnect_account(ctx, DisconnectAccountParams(account_id=bare.id))
    assert out.status == "success"
    assert await ctx.store.get("youtube_accounts", bare.id) is None


async def test_disconnect_unknown_account_id_errors(ctx):
    out = await h.disconnect_account(ctx, DisconnectAccountParams(account_id="does-not-exist"))
    assert out.status == "error"


async def test_generate_content_ideas_persists_rows_disconnect_can_later_clean(ctx, account, monkeypatch):
    """AI-generated ideas go through a different write path (generate_content_ideas)
    than save_content_idea -- confirm those rows are equally cleaned up on disconnect,
    since they are also keyed by channel_id with no account_email."""
    class FakeAI:
        async def complete(self, prompt, model=None):
            class R:
                text = "1. AI Idea A\n2. AI Idea B"
            return R()
    ctx.ai = FakeAI()

    result = await h.generate_content_ideas(ctx, GenerateContentIdeasParams(
        channel_id="UCabc123", channel_title="Creator Channel", count=2, context=""))
    assert result.status == "success"
    assert len(result.data.items) == 2

    out = await h.disconnect_account(ctx, DisconnectAccountParams(account_id=account.id))
    assert out.status == "success"
    remaining = await h.list_content_ideas(ctx, ListIdeasParams(channel_id="UCabc123"))
    assert remaining.data.items == []
