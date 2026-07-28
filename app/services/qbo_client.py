"""
QuickBooks Online API client.

Responsibilities:
    - OAuth 2.0 authorization-code flow + refresh-token handling
    - Idempotent posting of Deposits / Purchases / Transfers using a stable
      request id (see `build_request_id`)
    - Pulling the cash-basis ProfitAndLoss report via the Reports API

This module intentionally does NOT decide what to post -- callers (the
transactions API) are responsible for only calling `post_transaction` for
transactions with review_status == approved and duplicate_status ==
canonical. This client's only accounting decision is which QBO entity type
to use for a given transaction_type, per the assignment's recommended
mapping.
"""
import hashlib
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.models.enums import TransactionType
from app.services.classification import CHART_OF_ACCOUNTS

logger = logging.getLogger(__name__)

_SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com"
_PROD_BASE = "https://quickbooks.api.intuit.com"
_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
_AUTH_BASE = "https://appcenter.intuit.com/connect/oauth2"
_CHART_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "chart_of_accounts.json"
_POSTING_SUFFIX = " Sync"

ACCOUNT_TYPE_MAP = {
    "Fixed Assets": "Fixed Asset",
    "Expenses": "Expense",
}

ACCOUNT_SUBTYPE_MAP = {
    "Checking": "Checking",
    "Savings": "Savings",
    "Machinery and Equipment": "MachineryAndEquipment",
    "Owner's Equity": "OwnersEquity",
    "Service/Fee Income": "ServiceFeeIncome",
    "Discounts/Refunds Given": "DiscountsRefundsGiven",
    "Supplies & Materials - COGS": "SuppliesMaterialsCogs",
    "Cost of Labor": "CostOfLabor",
    "Payroll Expenses": "PayrollExpenses",
    "Rent or Lease of Buildings": "RentOrLeaseOfBuildings",
    "Auto": "Auto",
    "Dues & Subscriptions": "DuesSubscriptions",
    "Advertising/Promotional": "AdvertisingPromotional",
    "Insurance": "Insurance",
    "Utilities": "Utilities",
    "Legal & Professional Fees": "LegalProfessionalFees",
    "Bank Charges": "BankCharges",
    "Office/General Administrative Expenses": "OfficeGeneralAdministrativeExpenses",
    "Repair & Maintenance": "RepairMaintenance",
}

BANK_POSTING_ACCOUNT_NAMES = {
    "1000": "Operating Checking Sync",
    "1010": "Tax Reserve Sync",
}

CHART_ACCOUNT_DEFS = {
    account["Account No."]: account
    for account in json.loads(_CHART_FILE.read_text())
}

# Recommended QBO entity per transaction type, per the assignment's guidance.
# JournalEntry is deliberately not the default -- Intuit recommends it only
# for genuine accounting-correction/adjustment cases.
ENTITY_FOR_TYPE = {
    TransactionType.REVENUE: "Deposit",
    TransactionType.REFUND: "Purchase",  # refund to customer = money out
    TransactionType.COGS: "Purchase",
    TransactionType.OPERATING_EXPENSE: "Purchase",
    TransactionType.TRANSFER: "Transfer",
    TransactionType.OWNER_CONTRIBUTION: "Deposit",
    TransactionType.OWNER_DISTRIBUTION: "Purchase",
    TransactionType.FIXED_ASSET_PURCHASE: "Purchase",
}


class QboAuthError(Exception):
    pass


class QboApiError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def build_request_id(realm_id: str, normalized_transaction_id: str) -> str:
    """A stable, deterministic request id. Retrying with the same
    (realm, transaction) pair always produces the same id, so QBO's
    idempotency check on the `requestid` query param prevents duplicate
    creation after a network failure and a naive retry."""
    raw = f"finz|{realm_id}|{normalized_transaction_id}"
    return "finz-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


class QboClient:
    def __init__(self, realm_id: str | None = None, access_token: str | None = None):
        settings = get_settings()
        self.settings = settings
        self.realm_id = realm_id or settings.qbo_realm_id
        self.access_token = access_token or settings.qbo_access_token
        self.base_url = _SANDBOX_BASE if settings.qbo_environment == "sandbox" else _PROD_BASE
        self._account_ref_cache: dict[str, dict[str, str]] = {}

    # ---- OAuth ----

    def authorization_url(self, state: str) -> str:
        settings = self.settings
        return (
            f"{_AUTH_BASE}?client_id={settings.qbo_client_id}"
            f"&redirect_uri={settings.qbo_redirect_uri}"
            f"&response_type=code&scope=com.intuit.quickbooks.accounting"
            f"&state={state}"
        )

    async def exchange_code_for_tokens(self, code: str, realm_id: str) -> dict:
        settings = self.settings
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.qbo_redirect_uri,
                },
                auth=(settings.qbo_client_id, settings.qbo_client_secret),
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise QboAuthError(f"Token exchange failed: {resp.status_code} {resp.text}")
        data = resp.json()
        self.realm_id = realm_id
        self.access_token = data["access_token"]
        return data  # caller persists access_token / refresh_token securely

    async def refresh_access_token(self, refresh_token: str) -> dict:
        settings = self.settings
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                auth=(settings.qbo_client_id, settings.qbo_client_secret),
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise QboAuthError(f"Token refresh failed: {resp.status_code} {resp.text}")
        data = resp.json()
        self.access_token = data["access_token"]
        return data

    def _headers(self) -> dict:
        if not self.access_token:
            raise QboAuthError("No QuickBooks access token available -- connect the sandbox first")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def account_ref(self, account_number: str) -> dict[str, str]:
        """Resolve our chart account number to QBO's internal AccountRef."""
        if account_number in self._account_ref_cache:
            return self._account_ref_cache[account_number]

        account_name = CHART_OF_ACCOUNTS.get(account_number)
        if not account_name:
            raise QboApiError(400, f"Unknown account number {account_number}")

        if account_number in BANK_POSTING_ACCOUNT_NAMES:
            posting_name = BANK_POSTING_ACCOUNT_NAMES[account_number]
            account = await self.find_account_by_name(posting_name)
            if not account:
                account = await self.create_account(
                    name=posting_name,
                    account_type="Bank",
                    account_subtype="Checking" if account_number == "1000" else "Savings",
                )
            ref = {"value": account["Id"], "name": posting_name}
            self._account_ref_cache[account_number] = ref
            return ref

        account_def = CHART_ACCOUNT_DEFS.get(account_number)
        posting_name = f"{account_name}{_POSTING_SUFFIX}"
        account = await self.find_account_by_name(posting_name)
        if not account and account_def:
            account = await self.create_account(
                name=posting_name,
                account_type=account_def["QBO Account Type"],
                account_subtype=account_def.get("Suggested Detail Type") or None,
            )
        if not account:
            account = await self.find_account_by_name(account_name)
        if not account and account_number == "3000":
            equity_accounts = await self.find_accounts_by_type("Equity")
            account = equity_accounts[0] if equity_accounts else None
        if not account:
            raise QboApiError(400, f"QBO account not found by name: {account_name}")

        ref = {"value": account["Id"], "name": account["Name"]}
        self._account_ref_cache[account_number] = ref
        return ref

    async def find_accounts_by_type(self, account_type: str) -> list[dict]:
        escaped_type = account_type.replace("'", "\\'")
        query = f"select * from Account where AccountType = '{escaped_type}'"
        url = f"{self.base_url}/v3/company/{self.realm_id}/query"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                params={"query": query, "minorversion": "65"},
            )
        if resp.status_code >= 400:
            raise QboApiError(resp.status_code, resp.text)
        return resp.json().get("QueryResponse", {}).get("Account", [])

    async def find_account_by_name(self, account_name: str) -> dict | None:
        accounts = await self.find_accounts_by_name(account_name)
        if not accounts:
            return None

        def created_at(account: dict) -> str:
            return account.get("MetaData", {}).get("CreateTime", "")

        active_accounts = [account for account in accounts if account.get("Active", True)]
        candidates = active_accounts or accounts
        return max(candidates, key=created_at)

    async def find_accounts_by_name(self, account_name: str) -> list[dict]:
        escaped_name = account_name.replace("'", "\\'")
        query = f"select * from Account where Name = '{escaped_name}'"
        url = f"{self.base_url}/v3/company/{self.realm_id}/query"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                params={"query": query, "minorversion": "65"},
            )
        if resp.status_code >= 400:
            raise QboApiError(resp.status_code, resp.text)
        return resp.json().get("QueryResponse", {}).get("Account", [])

    async def create_account(
        self,
        *,
        name: str,
        account_type: str,
        account_subtype: str | None = None,
    ) -> dict:
        account_type = ACCOUNT_TYPE_MAP.get(account_type, account_type)
        account_subtype = ACCOUNT_SUBTYPE_MAP.get(account_subtype, account_subtype)
        body = {"Name": name, "AccountType": account_type}
        if account_subtype:
            body["AccountSubType"] = account_subtype

        url = f"{self.base_url}/v3/company/{self.realm_id}/account"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                params={"minorversion": "65"},
                json=body,
            )

        if resp.status_code >= 400 and account_subtype:
            # Detail types vary across QBO sandboxes. If Intuit rejects the
            # suggested subtype, create the account with only the account type.
            body.pop("AccountSubType", None)
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    url,
                    headers=self._headers(),
                    params={"minorversion": "65"},
                    json=body,
                )

        if resp.status_code >= 400:
            raise QboApiError(resp.status_code, resp.text)
        return resp.json().get("Account", resp.json())

    # ---- Posting ----

    async def post_transaction(self, txn: dict, request_id: str) -> dict:
        """Posts one normalized transaction to QBO using the entity implied
        by its transaction_type. Uses `requestid` for idempotency: retrying
        the exact same request_id after a network failure is safe and will
        not create a duplicate on Intuit's side.
        """
        entity = ENTITY_FOR_TYPE.get(txn["transaction_type"])
        if entity is None:
            raise QboApiError(400, f"No QBO entity mapping for transaction_type={txn['transaction_type']}")

        body = await self._build_entity_body(entity, txn)
        url = f"{self.base_url}/v3/company/{self.realm_id}/{entity.lower()}?requestid={request_id}&minorversion=65"

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=self._headers(), json=body)

        if resp.status_code >= 400:
            raise QboApiError(resp.status_code, resp.text)

        data = resp.json()
        qbo_id = data.get(entity, {}).get("Id")
        return {"qbo_entity_type": entity, "qbo_transaction_id": qbo_id, "raw_response": data}

    async def _build_entity_body(self, entity: str, txn: dict) -> dict[str, Any]:
        amount = abs(txn["amount_cents"]) / 100
        account_ref = await self.account_ref(txn["qbo_account_number"])
        bank_account_ref = await self.account_ref("1000" if txn["bank_account"] == "Operating Checking" else "1010")

        if entity == "Deposit":
            return {
                "TxnDate": str(txn["transaction_date"]),
                "DepositToAccountRef": bank_account_ref,
                "Line": [{
                    "Amount": amount,
                    "DetailType": "DepositLineDetail",
                    "DepositLineDetail": {"AccountRef": account_ref},
                    "Description": txn["description_normalized"],
                }],
            }
        if entity == "Purchase":
            return {
                "TxnDate": str(txn["transaction_date"]),
                "AccountRef": bank_account_ref,
                "PaymentType": "Check",
                "Line": [{
                    "Amount": amount,
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {"AccountRef": account_ref},
                    "Description": txn["description_normalized"],
                }],
            }
        if entity == "Transfer":
            from_ref = bank_account_ref
            to_ref = await self.account_ref("1010" if txn["bank_account"] == "Operating Checking" else "1000")
            return {
                "TxnDate": str(txn["transaction_date"]),
                "Amount": amount,
                "FromAccountRef": from_ref,
                "ToAccountRef": to_ref,
            }
        raise QboApiError(400, f"Unsupported entity type {entity}")

    # ---- Reporting ----

    async def get_profit_and_loss(self, start: date, end: date) -> dict:
        url = (
            f"{self.base_url}/v3/company/{self.realm_id}/reports/ProfitAndLoss"
            f"?start_date={start.isoformat()}&end_date={end.isoformat()}"
            f"&accounting_method=Cash&minorversion=65"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self._headers())
        if resp.status_code >= 400:
            raise QboApiError(resp.status_code, resp.text)
        return resp.json()


def parse_pnl_report_to_account_totals(report: dict) -> dict[str, int]:
    """Flattens QuickBooks' nested ProfitAndLoss report JSON into
    {account_name: amount_cents} so it can be diffed line-by-line against
    the application's own P&L. QBO's report structure nests Income under
    Rows.Row[...].Rows.Row[...] recursively -- this walks all of it."""
    totals: dict[str, int] = {}

    def walk(rows: list[dict]) -> None:
        for row in rows:
            if row.get("type") == "Data" and "ColData" in row:
                name = row["ColData"][0].get("value")
                if name and name.endswith(_POSTING_SUFFIX):
                    name = name[: -len(_POSTING_SUFFIX)]
                try:
                    amount = float(row["ColData"][-1].get("value", "0") or "0")
                except ValueError:
                    amount = 0.0
                if name:
                    totals[name] = totals.get(name, 0) + round(amount * 100)
            nested = row.get("Rows", {}).get("Row", [])
            if nested:
                walk(nested)

    rows = report.get("Rows", {}).get("Row", [])
    walk(rows)
    return totals
