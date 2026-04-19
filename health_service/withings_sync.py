"""
Withings sync — disabled pending Infisical rebuild.

When Infisical is back up, restore credentials here and re-enable sync.
"""

import logging

logger = logging.getLogger(__name__)


def sync_withings(days_back: int = 7):
    logger.info("Withings sync skipped — credentials not configured (Infisical pending rebuild)")
    return {"status": "skipped", "reason": "credentials not configured"}
