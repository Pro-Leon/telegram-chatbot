"""P3.1 — Bounded context limits for LLM context enrichment.

All limits are positive integers enforced at import time.
Centralized here so context_assembler.py and config.py stay in sync.
"""

MAX_CONTEXT_MESSAGES: int = 30
MAX_PURCHASE_HISTORY: int = 5
MAX_ACTIVE_OFFERS: int = 3
