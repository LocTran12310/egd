"""EGD — Evidence-Gated Delivery.

Plans live as files, progress lives as an append-only event log, and every gate
is a function of both. Nothing here depends on anything outside the standard
library.
"""

__version__ = "0.1.7"

# Bumped whenever a gate rule changes meaning, so a plan authored under older
# rules is flagged instead of failing mysteriously.
# v2: vague words in acceptance criteria close `clarify` (vague.py); v1 plans are only warned.
# v3: a proof that also passes without the feature's changes closes `build`, and on the `full` tier
#     every test/cli proof must have been checked that way (proof/baseline.py); v2 plans are warned.
RULES = 3
