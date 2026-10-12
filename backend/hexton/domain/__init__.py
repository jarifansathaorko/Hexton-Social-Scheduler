"""Pure domain rules. No I/O, no database, no network, no framework imports.

Everything in this package is a deterministic function of its arguments, so it can be
tested exhaustively and reused from the API, the worker and the frontend contract alike.

Source of truth for the vocabulary: ``02-technical-prd.md`` section 4, and the Phase 0
proof of concept in ``phase0-poc/``.
"""
