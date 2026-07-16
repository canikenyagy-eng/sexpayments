"""Access policies for the receipts / receipt-moderation feature.

No user-facing role permissions beyond the generic ones in
``users.permissions`` (``require_admin``, etc.) — re-exported here for
symmetry with the module structure (per CLAUDE.md) and as the home for any
future fine-grained checks. The bot-to-backend endpoint uses
``X-Bot-Secret`` instead of a JWT — see ``app/api/bot/v1/endpoints/support.py``.
"""

# Admin-only endpoints in this domain import the guard from here so any future
# swap is a single-file change.
from app.modules.users.permissions import require_admin  # noqa: F401
