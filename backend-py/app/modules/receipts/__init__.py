"""Receipts — the unified store + lifecycle for every uploaded receipt/proof.

Layout::

    models.py        # Receipt + ReceiptModeration (ORM)
    repository.py     # ReceiptRepository + ReceiptModerationRepository
    service.py        # ReceiptService — the upload() use case orchestrator
    moderation/       # one cohesive moderation component (decision + human review)
    effects.py        # deferred side-effects (trader notify / fraud / cascade)
    storage.py        # filesystem side of a receipt (save / size / format guard)
    download.py       # SSRF-safe fetch of a merchant-supplied evidence URL
    schemas/          # read.py (cross-role item) + admin.py / bot.py (moderation)
    exceptions.py / permissions.py

**Accepted deviation from CLAUDE.md** ("infrastructure -> app/infrastructure/"):
``storage.py`` and ``download.py`` are filesystem/HTTP infra but live here on
purpose -- they are receipt-specific, kept cohesive with the domain, and are
small/swappable (local disk today, S3 later) without touching callers. If a
second consumer ever needs them, promote to ``app/infrastructure/receipts/``.
"""
