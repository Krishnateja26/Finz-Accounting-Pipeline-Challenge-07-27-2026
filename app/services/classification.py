"""
Classification: rules first, Gemini second, human review last.

`classify_deterministic` never calls out to Gemini or the DB -- it is a pure
function over (description, amount_cents) that either returns a confident
classification or returns None, signalling the caller should fall back to
`gemini_classifier` and then to manual review.

Rule order matters: more specific vendor rules are checked before generic
keyword rules (e.g. "PRECISION INSTALL SVCS" must hit the subcontractor rule
before the generic "INSTALL" -> Installation Revenue keyword rule).
"""
import re
from dataclasses import dataclass

from app.models.enums import TransactionType

CHART_OF_ACCOUNTS = {
    "1000": "Operating Checking",
    "1010": "Tax Reserve",
    "1500": "Tools & Equipment",
    "3000": "Owner's Equity",
    "4000": "Repair Service Revenue",
    "4010": "Installation Revenue",
    "4020": "Maintenance Plan Revenue",
    "4100": "Customer Refunds",
    "5000": "Materials & Supplies",
    "5010": "Subcontractor Costs",
    "6000": "Payroll Expense",
    "6010": "Rent Expense",
    "6020": "Vehicle & Fuel",
    "6030": "Software & Subscriptions",
    "6040": "Marketing & Advertising",
    "6050": "Insurance Expense",
    "6060": "Utilities",
    "6070": "Professional Fees",
    "6080": "Bank Fees",
    "6090": "Office & General",
    "6100": "Repairs & Maintenance",
}


@dataclass
class ClassificationResult:
    transaction_type: TransactionType
    account_number: str
    counterparty: str | None
    confidence: float
    explanation: str
    source: str = "deterministic_rule"


@dataclass
class _Rule:
    keywords: tuple[str, ...]
    account_number: str
    transaction_type: TransactionType
    explanation: str
    amount_sign: str = "any"  # "positive" | "negative" | "any"


# Order matters -- first match wins. Vendor-specific expense rules are placed
# ahead of generic revenue keywords (e.g. "INSTALL") so a subcontractor named
# "Precision Install Svcs" is never mistaken for installation revenue.
_RULES: list[_Rule] = [
    # --- Refunds ---
    _Rule(("REFUND TO",), "4100", TransactionType.REFUND,
          "Description indicates a refund paid to a customer", "negative"),

    # --- Owner activity ---
    _Rule(("OWNER DISTRIBUTION",), "3000", TransactionType.OWNER_DISTRIBUTION,
          "Description indicates an owner distribution", "negative"),
    _Rule(("OWNER CAPITAL",), "3000", TransactionType.OWNER_CONTRIBUTION,
          "Description indicates an owner capital contribution", "positive"),

    # --- Internal transfers (excluded from P&L) ---
    _Rule(("TAX RESERVE TRANSFER", "ONLINE TRANSFER"), None, TransactionType.TRANSFER,
          "Description indicates an internal transfer between company bank accounts"),

    # --- Fixed asset purchase ---
    _Rule(("MILWAUKEE COMMERCIAL TOOL PACKAGE",), "1500", TransactionType.FIXED_ASSET_PURCHASE,
          "Description matches a known equipment/tool purchase (capitalized, not expensed)", "negative"),

    # --- Subcontractor costs (COGS) -- vendor-specific, checked before generic 'INSTALL' keyword ---
    _Rule(("APEX ELECTRICAL", "APEX ELEC"), "5010", TransactionType.COGS,
          "Payment to subcontractor Apex Electrical", "negative"),
    _Rule(("NORTHLINE HVAC",), "5010", TransactionType.COGS,
          "Payment to subcontractor Northline HVAC", "negative"),
    _Rule(("RIVERA PLUMBING", "RIVERA PLBG"), "5010", TransactionType.COGS,
          "Payment to subcontractor Rivera Plumbing", "negative"),
    _Rule(("METRO HANDYMAN",), "5010", TransactionType.COGS,
          "Payment to subcontractor Metro Handyman", "negative"),
    _Rule(("PRECISION INSTALL SVCS",), "5010", TransactionType.COGS,
          "Payment to subcontractor Precision Install Svcs", "negative"),

    # --- Materials & supplies (COGS) -- vendor-specific ---
    _Rule(("HOME DEPOT", "HOMEDEPOT"), "5000", TransactionType.COGS,
          "Purchase from Home Depot (materials supplier)", "negative"),
    _Rule(("LOWE'S", "LOWES"), "5000", TransactionType.COGS,
          "Purchase from Lowe's (materials supplier)", "negative"),
    _Rule(("FERGUSON",), "5000", TransactionType.COGS,
          "Purchase from Ferguson (materials supplier)", "negative"),
    _Rule(("WW GRAINGER", "GRAINGER"), "5000", TransactionType.COGS,
          "Purchase from Grainger (materials supplier)", "negative"),
    _Rule(("SUPPLYHOUSE",), "5000", TransactionType.COGS,
          "Purchase from SupplyHouse.com (materials supplier)", "negative"),
    _Rule(("ABC PLUMBING SUPPLY",), "5000", TransactionType.COGS,
          "Purchase from ABC Plumbing Supply (materials supplier)", "negative"),
    _Rule(("CES #NYC",), "5000", TransactionType.COGS,
          "Purchase from CES materials supplier", "negative"),

    # --- Operating expenses -- vendor-specific ---
    _Rule(("ADP PAYROLL",), "6000", TransactionType.OPERATING_EXPENSE,
          "ADP payroll run", "negative"),
    _Rule(("PARKSIDE COMMERCIAL MGMT", "RENT"), "6010", TransactionType.OPERATING_EXPENSE,
          "Rent payment to Parkside Commercial Management", "negative"),
    _Rule(("SHELL OIL", "EXXONMOBIL", "SPEEDWAY", "BP#"), "6020", TransactionType.OPERATING_EXPENSE,
          "Fuel purchase at a gas station", "negative"),
    _Rule(("INTUIT", "QUICKBOOKS", "GOOGLE WORKSPACE", "SERVICETITAN"), "6030", TransactionType.OPERATING_EXPENSE,
          "Software / SaaS subscription charge", "negative"),
    _Rule(("GOOGLE ADS", "YELP"), "6040", TransactionType.OPERATING_EXPENSE,
          "Advertising / marketing platform charge", "negative"),
    _Rule(("HISCOX",), "6050", TransactionType.OPERATING_EXPENSE,
          "Hiscox business insurance premium", "negative"),
    _Rule(("CON EDISON", "VERIZON"), "6060", TransactionType.OPERATING_EXPENSE,
          "Utility bill payment", "negative"),
    _Rule(("CLEARLEDGER CPA",), "6070", TransactionType.OPERATING_EXPENSE,
          "Accounting / professional services fee", "negative"),
    _Rule(("MONTHLY SERVICE FEE",), "6080", TransactionType.OPERATING_EXPENSE,
          "Bank monthly service charge", "negative"),
    _Rule(("STAPLES",), "6090", TransactionType.OPERATING_EXPENSE,
          "Office supplies purchase", "negative"),
    _Rule(("FLEET AUTO CARE",), "6100", TransactionType.OPERATING_EXPENSE,
          "Vehicle repair/maintenance service", "negative"),

    # --- Revenue keyword rules (generic, checked last so vendor-specific
    #     expense rules above always win first) ---
    _Rule(("MAINT PLAN", "SERVICE PLAN"), "4020", TransactionType.REVENUE,
          "Customer receipt referencing a maintenance/service plan", "positive"),
    _Rule(("INSTALL", "PROJECT"), "4010", TransactionType.REVENUE,
          "Customer receipt referencing an installation project", "positive"),
]

# Prefixes stripped when extracting a human-readable counterparty name from a
# bank description, e.g. "ACH CREDIT BLUEBIRD PROPERTY MANAGEMENT INV 4100"
# -> "Bluebird Property Management".
_PREFIXES = [
    "ACH CREDIT", "ACH REFUND TO", "ACH", "CHECK DEPOSIT", "CHECK DEP",
    "MOBILE DEPOSIT", "ZELLE FROM", "WIRE FROM", "WIRE", "AUTO PAY",
]
_SUFFIX_RE = re.compile(
    r"\b(INV|JOB|PROJECT|SERVICE|INSTALL|MAINT PLAN|SERVICE PLAN|REPAIR|EQUIPMENT INSTALL|"
    r"APR|MAY|JUN|FIRST HALF|SECOND HALF|REF)\b.*$",
    re.IGNORECASE,
)


def extract_counterparty(description_normalized: str) -> str | None:
    text = description_normalized.strip()
    upper = text.upper()
    for prefix in _PREFIXES:
        if upper.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    text = _SUFFIX_RE.sub("", text).strip(" -")
    if not text or text.upper() == description_normalized.upper():
        return None
    return text.title()


def classify_deterministic(description_normalized: str, amount_cents: int) -> ClassificationResult | None:
    upper = description_normalized.upper()
    sign = "positive" if amount_cents > 0 else "negative"

    for rule in _RULES:
        if rule.amount_sign != "any" and rule.amount_sign != sign:
            continue
        if any(kw in upper for kw in rule.keywords):
            return ClassificationResult(
                transaction_type=rule.transaction_type,
                account_number=rule.account_number,
                counterparty=extract_counterparty(description_normalized),
                confidence=0.98,
                explanation=rule.explanation,
            )

    # Default fallback for an unmatched customer receipt: treat as generic
    # repair service revenue only when it clearly looks like a customer
    # payment (positive amount with a recognizable deposit-style prefix).
    if sign == "positive" and any(
        upper.startswith(p) for p in ("ACH CREDIT", "CHECK DEP", "MOBILE DEPOSIT", "ZELLE FROM", "WIRE")
    ):
        return ClassificationResult(
            transaction_type=TransactionType.REVENUE,
            account_number="4000",
            counterparty=extract_counterparty(description_normalized),
            confidence=0.75,
            explanation="Customer receipt without installation or maintenance-plan indicators; "
                        "defaulted to Repair Service Revenue",
        )

    return None  # unmatched -- caller should try Gemini, then require manual review
