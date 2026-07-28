"""
One-time setup script: creates the chart of accounts from
data/chart_of_accounts.json in a connected QuickBooks Online sandbox.

Usage:
    python -m scripts.setup_qbo_chart_of_accounts

Requires QBO_REALM_ID and QBO_ACCESS_TOKEN to already be set in the
environment (run the /api/quickbooks/connect + /callback OAuth flow first).

The 1000/1010 bank accounts and 3000 Owner's Equity account already exist by
default in a fresh QBO sandbox company under slightly different names in
some cases -- this script checks for an existing account with the same name
before creating a new one, and reports any it had to skip so you can
reconcile the name/detail-type manually per the assignment's instruction to
"document any setup choices or QBO detail-type differences."
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "chart_of_accounts.json"


async def main() -> None:
    settings = get_settings()
    if not (settings.qbo_realm_id and settings.qbo_access_token):
        raise SystemExit("Set QBO_REALM_ID and QBO_ACCESS_TOKEN in the environment first (see README).")

    base = "https://sandbox-quickbooks.api.intuit.com" if settings.qbo_environment == "sandbox" \
        else "https://quickbooks.api.intuit.com"
    headers = {
        "Authorization": f"Bearer {settings.qbo_access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    accounts = json.loads(DATA_FILE.read_text())
    created, skipped = [], []

    async with httpx.AsyncClient(timeout=20) as client:
        for acct in accounts:
            name = acct["Account Name"]
            query = f"select * from Account where Name = '{name}'"
            resp = await client.get(
                f"{base}/v3/company/{settings.qbo_realm_id}/query",
                headers=headers, params={"query": query, "minorversion": "65"},
            )
            existing = resp.json().get("QueryResponse", {}).get("Account", [])
            if existing:
                skipped.append(name)
                logger.info("Skipping %s -- already exists in sandbox", name)
                continue

            body = {
                "Name": name,
                "AccountType": acct["QBO Account Type"],
                "AccountSubType": acct.get("Suggested Detail Type") or None,
            }
            resp = await client.post(
                f"{base}/v3/company/{settings.qbo_realm_id}/account",
                headers=headers, params={"minorversion": "65"}, json=body,
            )
            if resp.status_code >= 400:
                logger.error("Failed to create %s: %s %s", name, resp.status_code, resp.text)
                # A rejected AccountSubType is a common cause -- fall back to
                # letting QBO pick the default detail type for the account type.
                body.pop("AccountSubType", None)
                resp = await client.post(
                    f"{base}/v3/company/{settings.qbo_realm_id}/account",
                    headers=headers, params={"minorversion": "65"}, json=body,
                )
            if resp.status_code < 400:
                created.append(name)
                logger.info("Created %s", name)
            else:
                logger.error("Still failed to create %s: %s", name, resp.text)

    logger.info("Done. Created: %d, Skipped (already existed): %d", len(created), len(skipped))
    logger.info("Created: %s", created)
    logger.info("Skipped: %s", skipped)


if __name__ == "__main__":
    asyncio.run(main())
