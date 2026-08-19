"""Connected-account resolution -- mirrors Google Drive Connector's
accounts.py pattern exactly (same field names, same resolve/disconnect
shape) so this app's OAuth lifecycle behaves identically to the one
already proven in production.
"""

from __future__ import annotations

import youtube_client as yc

ACCOUNTS = "youtube_accounts"
UNKNOWN_EMAILS = {"", "unknown", "unknown@unknown", "google account"}


def account_email(doc) -> str:
    return str((doc.data or {}).get("email") or "").strip()


def account_label(doc) -> str:
    email = account_email(doc)
    if email.lower() not in UNKNOWN_EMAILS:
        return email
    name = str((doc.data or {}).get("display_name") or "").strip()
    return name if name and name.lower() != "unknown" else "Google account needs reconnecting"


def identity_missing(doc) -> bool:
    return account_email(doc).lower() in UNKNOWN_EMAILS


async def all_accounts(ctx) -> list:
    page = await ctx.store.query(ACCOUNTS, limit=100)
    return list(page.data)


async def resolve_account(ctx, reference: str = "") -> dict:
    docs = await all_accounts(ctx)
    if not docs:
        return yc.fail(yc.ACCOUNT_MISSING)
    wanted = (reference or "").strip().lower()
    if wanted:
        matches = [d for d in docs if account_email(d).lower() == wanted]
        if not matches:
            matches = [d for d in docs if wanted in account_email(d).lower()]
        if not matches:
            emails = ", ".join(account_email(d) or "unknown" for d in docs)
            return yc.fail(yc.ACCOUNT_MISSING, f"No connected account matches '{reference}'. Connected: {emails}")
        return {"ok": True, "account": matches[0]}
    active = [d for d in docs if bool((d.data or {}).get("is_active"))]
    if active:
        return {"ok": True, "account": active[0]}
    if len(docs) == 1:
        return {"ok": True, "account": docs[0]}
    emails = ", ".join(account_email(d) or "unknown" for d in docs)
    return yc.fail(yc.ACCOUNT_AMBIGUOUS, f"Several Google accounts are connected ({emails}); name one.")


async def account_for_channel(ctx, channel_id: str) -> dict:
    """Resolve which connected account owns a given channel id, by checking
    each account's cached channel list. Falls back to a fresh channels.list
    call per account if no cache entry matches (cache may be stale/absent)."""
    docs = await all_accounts(ctx)
    if not docs:
        return yc.fail(yc.ACCOUNT_MISSING)
    for doc in docs:
        cached_ids = set((doc.data or {}).get("channel_ids") or [])
        if channel_id in cached_ids:
            return {"ok": True, "account": doc}
    # Fall back: ask each account directly (bounded by how few accounts a
    # user realistically connects -- not a scan of all YouTube).
    for doc in docs:
        out = await yc.api_call(ctx, doc, "GET", "/channels", params={"part": "id", "id": channel_id, "mine": "false"})
        if out.get("ok") and (out["data"].get("items") or []):
            return {"ok": True, "account": doc}
    return yc.fail(yc.NOT_FOUND, "No connected Google account can access that channel.")


async def disconnect(ctx, account_id: str) -> dict:
    """Remove one OAuth account record and its account-scoped local data.

    Content ideas are stored keyed by channel_id, never by account_email --
    no idea document has ever carried an account_email field, so a lookup
    of youtube_content_ideas by account_email always returned zero rows.
    That silently broke this function's own documented promise ("Its local
    YouTube Studio Hub data (saved ideas) is removed"): disconnecting an
    account never actually deleted its ideas. Fixed by matching ideas
    through the account's own cached channel_ids instead, the same join
    key account_for_channel() already relies on elsewhere in this file.
    youtube_settings is intentionally left out of this cleanup: it holds
    one single app-wide scope="app" document, not per-account data, so
    there is nothing account-scoped to remove there.
    """
    doc = await ctx.store.get(ACCOUNTS, account_id)
    if not doc:
        return yc.fail(yc.ACCOUNT_MISSING, "That Google account is no longer connected.")
    channel_ids = set((doc.data or {}).get("channel_ids") or [])
    if channel_ids:
        page = await ctx.store.query("youtube_content_ideas", limit=500)
        for item in page.data:
            if (item.data or {}).get("channel_id") in channel_ids:
                await ctx.store.delete("youtube_content_ideas", item.id)
    await ctx.store.delete(ACCOUNTS, account_id)
    remaining = await all_accounts(ctx)
    if remaining and not any(bool((x.data or {}).get("is_active")) for x in remaining):
        await ctx.store.update(ACCOUNTS, remaining[0].id, {"is_active": True})
    return {"ok": True, "account_id": account_id, "label": account_label(doc)}


async def activate(ctx, reference: str) -> dict:
    resolved = await resolve_account(ctx, reference)
    if not resolved.get("ok"):
        return resolved
    doc = resolved["account"]
    for other in await all_accounts(ctx):
        if other.id != doc.id and bool((other.data or {}).get("is_active")):
            await ctx.store.update(ACCOUNTS, other.id, {"is_active": False})
    await ctx.store.update(ACCOUNTS, doc.id, {"is_active": True})
    return {"ok": True, "account": await ctx.store.get(ACCOUNTS, doc.id)}


async def app_setting(ctx) -> dict:
    page = await ctx.store.query("youtube_settings", where={"scope": "app"}, limit=1)
    if page.data:
        return dict(page.data[0].data or {})
    return {}


async def save_app_setting(ctx, **fields) -> dict:
    page = await ctx.store.query("youtube_settings", where={"scope": "app"}, limit=1)
    if page.data:
        doc = await ctx.store.update("youtube_settings", page.data[0].id, fields)
    else:
        doc = await ctx.store.create("youtube_settings", {"scope": "app", **fields})
    return dict(doc.data or {})
