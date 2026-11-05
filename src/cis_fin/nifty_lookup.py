"""Look up an exact company name in the official Nifty 50 constituent CSV."""

import csv
import io
import json
import os
import re
import unicodedata
from pathlib import Path
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


NIFTY_50_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"


class NiftyLookupError(Exception):
    """The official constituent list could not be queried or matched safely."""


@dataclass(frozen=True)
class NiftyCompany:
    company_name: str
    symbol: str
    industry: str
    sector: str = ""
    source_url: str = NIFTY_50_CSV_URL
    classification_source_url: str = ""


# Temporary outage fallback for exact, unambiguous names. The official lookup
# remains the primary source; these values only prevent a transient 502 from
# leaving a known company without a ticker.
KNOWN_TICKER_FALLBACKS = {
    "tata steel": "TATASTEEL",
}

COMPANY_NAME_ALIASES = {
    "hcltech": "hcl technologies",
    "hcl technologies": "hcl technologies",
}

LOCAL_NIFTY500_PATH = Path(__file__).resolve().parents[2] / "data" / "nifty500.csv"
MASTER_EQUITY_PATH = Path(__file__).resolve().parents[2] / "BSE NSE MasterEquity list.csv"


def _normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value).casefold()
    tokens = value.split()
    # Official constituent files vary between "Ltd." and "Limited".
    while tokens and tokens[-1] in {"limited", "ltd", "company", "co", "corporation", "corp"}:
        tokens.pop()
    return " ".join(tokens)


def _name_alias(value: str) -> str:
    normalized = _normalize_name(value)
    return COMPANY_NAME_ALIASES.get(normalized, normalized)

def lookup_master_equity(company_name: str = "", symbol: str = "") -> NiftyCompany:
    """Look up company master data, preferring NSE code and falling back to BSE code."""
    if not MASTER_EQUITY_PATH.exists():
        raise NiftyLookupError("The BSE/NSE MasterEquity list is not available.")
    wanted_name = _name_alias(company_name)
    wanted_symbol = symbol.strip().upper()
    try:
        with MASTER_EQUITY_PATH.open(newline="", encoding="utf-8-sig") as source:
            matches = []
            for row in csv.DictReader(source):
                name = (row.get("Issuer Name") or "").strip()
                bse = (row.get("BSE Code") or "").strip().upper()
                nse = (row.get("NSE Code") or "").strip().upper()
                if not name:
                    continue
                if (wanted_name and _name_alias(name) == wanted_name) or (wanted_symbol and wanted_symbol in {nse, bse}):
                    matches.append((name, nse, bse, row))
            if matches:
                preferred = next((item for item in matches if item[1]), matches[0])
                name, nse, bse, row = preferred
                # The file commonly stores classification on the BSE row and
                # the NSE code on a second duplicate row. Merge both rows.
                sector = next(((item[3].get("Sector Name") or "").strip() for item in matches if (item[3].get("Sector Name") or "").strip()), "")
                industry = next(((item[3].get("Industry Name") or "").strip() for item in matches if (item[3].get("Industry Name") or "").strip()), "")
                return NiftyCompany(name, nse or bse, industry, sector, str(MASTER_EQUITY_PATH))
    except (OSError, csv.Error) as exc:
        raise NiftyLookupError("Could not read the BSE/NSE MasterEquity list.") from exc
    raise NiftyLookupError("No exact company-name or NSE/BSE symbol match was found in the MasterEquity list.")

def find_master_equity_hint(text: str) -> NiftyCompany:
    """Find a conservative master-list match in a PDF filename or label."""
    normalized = _normalize_name(text)
    if not normalized:
        raise NiftyLookupError("No company hint was provided.")
    if not MASTER_EQUITY_PATH.exists():
        raise NiftyLookupError("The BSE/NSE MasterEquity list is not available.")
    with MASTER_EQUITY_PATH.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            name = (row.get("Issuer Name") or "").strip()
            tokens = _normalize_name(name).split()
            if tokens and ((len(tokens) == 1 and tokens[0] in normalized.split()) or all(token in normalized.split() for token in tokens)):
                return lookup_master_equity(name)
    raise NiftyLookupError("No company hint matched the BSE/NSE MasterEquity list.")

def find_master_equity_in_text(text: str) -> NiftyCompany:
    """Match a company name printed in the opening pages of a report."""
    normalized = _normalize_name(text)
    if not normalized:
        raise NiftyLookupError("No report text was provided.")
    if not MASTER_EQUITY_PATH.exists():
        raise NiftyLookupError("The BSE/NSE MasterEquity list is not available.")
    with MASTER_EQUITY_PATH.open(newline="", encoding="utf-8-sig") as source:
        candidates = []
        for row in csv.DictReader(source):
            name = (row.get("Issuer Name") or "").strip()
            phrase = _name_alias(name)
            # Require the issuer name to occur as one contiguous phrase. The
            # previous token-by-token match could combine words from unrelated
            # companies mentioned in an annual report.
            aliases = {phrase, _normalize_name(name)}
            if any(" " + candidate + " " in " " + normalized + " " for candidate in aliases if len(candidate.split()) >= 1):
                padded_text = " " + normalized + " "
                positions = [padded_text.find(" " + candidate + " ") for candidate in aliases]
                position = min((p for p in positions if p >= 0), default=-1)
                if position >= 0:
                    candidates.append((position, -len(phrase), name))
        if candidates:
            candidates.sort()
            return lookup_master_equity(candidates[0][2])
    raise NiftyLookupError("No company name in the opening report pages matched the MasterEquity list.")


def fallback_ticker(company_name: str) -> str:
    """Return a locally verified fallback ticker for an exact normalized name."""
    return KNOWN_TICKER_FALLBACKS.get(_normalize_name(company_name), "")


def lookup_local_nifty500(company_name: str = "", symbol: str = "") -> NiftyCompany:
    """Match an exact company or symbol from the checked-in Nifty 500 snapshot."""
    if not LOCAL_NIFTY500_PATH.exists():
        raise NiftyLookupError("The local Nifty 500 reference file is not available.")
    try:
        with LOCAL_NIFTY500_PATH.open(newline="", encoding="utf-8-sig") as source:
            rows = csv.DictReader(source)
            wanted_name = _normalize_name(company_name)
            wanted_symbol = symbol.strip().upper()
            for row in rows:
                row_name = (row.get("Company Name") or "").strip()
                row_symbol = (row.get("Symbol") or "").strip().upper()
                if (
                    (wanted_name and _normalize_name(row_name) == wanted_name)
                    or (wanted_symbol and row_symbol == wanted_symbol)
                    or (wanted_name and wanted_name.replace(" ", "").upper() == row_symbol)
                ):
                    return NiftyCompany(
                        company_name=row_name,
                        symbol=row_symbol,
                        industry=(row.get("Industry") or "").strip(),
                    )
    except (OSError, csv.Error) as exc:
        raise NiftyLookupError("Could not read the local Nifty 500 reference file.") from exc
    raise NiftyLookupError("No exact company-name or ticker match was found in the local Nifty 500 list.")


def find_local_company_hint(text: str) -> NiftyCompany:
    """Find a conservative company hint from a filename or short label."""
    normalized = _normalize_name(text)
    if not normalized:
        raise NiftyLookupError("No company hint was provided.")
    if not LOCAL_NIFTY500_PATH.exists():
        raise NiftyLookupError("The local Nifty 500 reference file is not available.")
    with LOCAL_NIFTY500_PATH.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            company = (row.get("Company Name") or "").strip()
            company_tokens = _normalize_name(company).split()
            if len(company_tokens) == 1 and company_tokens[0] in normalized.split():
                return NiftyCompany(company, (row.get("Symbol") or "").strip().upper(), (row.get("Industry") or "").strip())
            if len(company_tokens) > 1 and all(token in normalized.split() for token in company_tokens):
                return NiftyCompany(company, (row.get("Symbol") or "").strip().upper(), (row.get("Industry") or "").strip())
    raise NiftyLookupError("No company hint matched the local Nifty 500 list.")


def parse_nifty50_csv(csv_text: str, company_name: str = "", symbol: str = "") -> NiftyCompany:
    """Return an exact name or symbol match; never guess from a partial name."""
    try:
        rows = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        required = {"Company Name", "Industry", "Symbol"}
        if not rows.fieldnames or not required.issubset(set(rows.fieldnames)):
            raise NiftyLookupError("The Nifty 50 CSV has an unexpected format.")
        wanted_name = _normalize_name(company_name)
        wanted_symbol = symbol.strip().upper()
        for row in rows:
            row_name = (row.get("Company Name") or "").strip()
            row_symbol = (row.get("Symbol") or "").strip().upper()
            if (wanted_name and _normalize_name(row_name) == wanted_name) or (
                not wanted_name and wanted_symbol and row_symbol == wanted_symbol
            ):
                return NiftyCompany(row_name, row_symbol, (row.get("Industry") or "").strip())
    except csv.Error as exc:
        raise NiftyLookupError("Could not read the official Nifty 50 constituent CSV.") from exc
    raise NiftyLookupError("No exact company-name or ticker match was found in the current Nifty 50 list.")


def lookup_nifty50(company_name: str = "", symbol: str = "", timeout_seconds: int = 15) -> NiftyCompany:
    if not company_name.strip() and not symbol.strip():
        raise NiftyLookupError("Enter a company name or ticker before looking it up.")
    request = Request(
        NIFTY_50_CSV_URL,
        headers={"User-Agent": "CIS-Fin-Research-Pipeline/1.0", "Accept": "text/csv,*/*"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if urlsplit(response.geturl()).hostname not in ("www.niftyindices.com", "niftyindices.com"):
                raise NiftyLookupError("The constituent list redirected away from the official Nifty Indices site.")
            content = response.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise NiftyLookupError("The official constituent CSV exceeded the size limit.")
        company = parse_nifty50_csv(content.decode("utf-8-sig"), company_name, symbol)
        # Search the broader web first. The NSE API's category labels do not always
        # match the sector/industry labels used by common market-information sites.
        sector, industry, classification_source = _lookup_tavily_classification(
            company.company_name, company.symbol, timeout_seconds
        )
        return NiftyCompany(
            company_name=company.company_name,
            symbol=company.symbol,
            industry=industry or company.industry,
            sector=sector,
            source_url=NIFTY_50_CSV_URL,
            classification_source_url=classification_source,
        )
    except HTTPError as exc:
        raise NiftyLookupError("Nifty Indices returned HTTP {} while fetching constituents.".format(exc.code)) from exc
    except (URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
        raise NiftyLookupError("Could not fetch the official Nifty 50 list. Check your internet connection and retry.") from exc


def _lookup_tavily_classification(company_name: str, symbol: str, timeout_seconds: int):
    """Use Tavily to find common stock-market classifications across the web."""
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return "", "", ""
    body = {
        "query": (
            'Search current market-information pages for "{}" (NSE symbol {}). '
            "I need the commonly reported business SECTOR and the narrower INDUSTRY. "
            "Do not copy an NSE macro-economic sector or map its Basic Industry into Sector. "
            "If the company's business is retail, use Retail or Retailing as the sector; Consumer Cyclical/Consumer Discretionary may be shown as the broader global sector. Do not return Consumer Services as the retail sector. "
            "For a speciality retailer, use Speciality Retail (or Apparel Retail where sources make that distinction) as the industry. "
            "If sources disagree, prefer those business-sector labels over an exchange taxonomy label. "
            "Reply in exactly two lines: SECTOR: <value> and INDUSTRY: <value>."
        ).format(company_name, symbol),
        "include_domains": [
            "screener.in", "moneycontrol.com", "groww.in", "trendlyne.com",
            "stockanalysis.com", "nseindia.com", "niftyindices.com",
        ],
        "include_domains_mode": "prefer",
        "search_depth": "advanced",
        "max_results": 5,
        "include_answer": True,
        "include_raw_content": True,
    }
    request = Request(
        "https://api.tavily.com/search",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(api_key),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read(2 * 1024 * 1024 + 1).decode("utf-8"))
        results = payload.get("results", [])
        sources = [
            result for result in results
            if urlsplit(result.get("url", "")).hostname
            and urlsplit(result.get("url", "")).scheme == "https"
        ]
        evidence = "\n".join(
            "{}\n{}\n{}".format(
                result.get("url", ""), result.get("content", ""), result.get("raw_content", "") or ""
            )
            for result in sources
        )
        answer = str(payload.get("answer") or "")
        text = answer + "\n" + evidence
        if symbol.casefold() not in text.casefold() and company_name.casefold() not in text.casefold():
            return "", "", ""
        sector = _extract_tavily_field(text, ("NSE Sector", "Sector"))
        industry = _extract_tavily_field(text, ("Basic Industry", "Industry"))
        source = "; ".join(result.get("url", "") for result in sources[:3])
        return sector, industry, source
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return "", "", ""


def _extract_tavily_field(text: str, labels):
    for label in labels:
        match = re.search(
            r"(?:^|[\n|;])\s*[\"']?(?:\*\*)?{}(?:\*\*)?[\"']?\s*[:|\-]\s*([^\n|;,}}]+)".format(re.escape(label)),
            text,
            flags=re.IGNORECASE,
        )
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" *.,")
            if value and value.casefold() not in {"not available", "n/a", "unknown"}:
                return value[:250]
    return ""
