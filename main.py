"""Entrypoint for Imperal validation, build and runtime loading."""

import os
import sys

_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

_LOCAL = (
    "app", "models", "youtube_client", "accounts", "converters", "handlers", "panels",
)
for _module in _LOCAL:
    sys.modules.pop(_module, None)

from app import chat, ext  # noqa: E402,F401
import handlers  # noqa: E402,F401
import panels  # noqa: E402,F401
