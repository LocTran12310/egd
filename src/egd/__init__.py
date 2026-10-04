"""EGD — Evidence-Gated Delivery.

Plans live as files, progress lives as an append-only event log, and every gate
is a function of both. Nothing here depends on anything outside the standard
library.
"""

__version__ = "0.1.0"

# Bumped whenever a gate rule changes meaning, so a plan authored under older
# rules is flagged instead of failing mysteriously.
RULES = 1
