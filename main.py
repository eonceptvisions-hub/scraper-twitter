#!/usr/bin/env python3
"""
Daily X (Twitter) Problem Scraper & Google Sheets Logger
This script automates lead generation by finding SaaS pain points discussed on X (Twitter).
It queries SerpApi for specific Google Search results and saves clean, deduplicated results 
to a Google Sheet using Service Account authentication.

Requirements:
- requests (for SerpApi calls)
- gspread (for Google Sheets integration)
- google-auth (for service account authentication)
"""

import os
import sys
import json
import logging
from datetime import datetime
import urllib.parse
import requests
import gspread
from google.oauth2.service_account import Credentials

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Mock data to simulate API responses in dry-run mode
MOCK_ORGANIC_RESULTS = [
    {
        "snippet": "It takes me hours to compile weekly status reports for our SaaS clients. Need a dashboard tool to automate this.",
        "link": "https://x.com/startup_founder_99/status/189283749281726"
    },
    {
        "snippet": "Setting up RBAC and user permission systems takes me hours to do from scratch in SaaS apps. Any templates?",
        "link": "https://twitter.com/maria_dev/status/192837482937482"
    },
    {
        "snippet": "Generating clean marketing visuals takes me hours to finish manually for our daily SaaS promotions.",
        "link": "https://x.com/marketing_sam/status/1728394827364"
    },
    {
        "snippet": "Normal non-X page snippet that should be filtered out.",
        "link": "https://example.com/blog/saas-productivity"
    }
]


def normalize_url(url: str) -> str:
    """
    Standardizes a URL to ensure accurate duplicate checking.
    - Strips spaces and converts domain to lowercase.
    - Resolves subdomains (like www. or mobile.) and protocols.
    - Uniformly represents Twitter as x.com.
    - Removes trailing slashes.
    """
    if not url:
        return ""
    url = url.strip().lower()
    
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]
        
        # Standardize domains to x.com
        if netloc in ["twitter.com", "mobile.twitter.com", "mobile.x.com"]:
            netloc = "x.com"
            
        normalized = parsed._replace(netloc=netloc, scheme="https", fragment="").geturl()
        if normalized.endswith("/"):
            normalized = normalized[:-1]
        return normalized
    except Exception:
        # Fallback to simple string manipulation if parsing fails
        url = url.replace("twitter.com", "x.com").replace("http://", "https://")
        if url.endswith("/"):
            url = url[:-1]
        return url


def is_valid_x_url(url: str) -> bool:
    """
    Checks if a URL belongs to a valid X (formerly Twitter) domain.
    """
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower()
        return any(d in domain for d in ["x.com", "twitter.com"])
    except Exception:
        return False


def fetch_serp_results(serpapi_key: str, dry_run: bool) -> list:
    """
    Queries SerpApi Google Search engine with a query designed to extract SaaS complaints.
    """
    if dry_run:
        logger.info("Dry-run: Simulating SerpApi Google Search.")
        return MOCK_ORGANIC_RESULTS
        
    query = 'site:x.com "takes me hours to" SaaS'
    url = "https://serpapi.com/search"
    params = {
        "engine": "google",
        "q": query,
        "api_key": serpapi_key,
        "hl": "en",
        "gl": "us",
        "num": 100  # Pull up to 100 results to maximize efficiency per call
    }
    
    logger.info(f"Querying SerpApi with query: '{query}'")
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # SerpApi specific error handling
        if "error" in data:
            logger.error(f"SerpApi returned an error: {data['error']}")
            sys.exit(1)
            
        organic_results = data.get("organic_results", [])
        logger.info(f"SerpApi returned {len(organic_results)} organic results.")
        return organic_results
    except Exception as e:
        logger.error(f"Failed to query SerpApi: {e}")
        sys.exit(1)


def clean_and_filter_results(results: list) -> list:
    """
    Cleans search results by dropping entries without valid X/Twitter URLs or snippets.
    """
    cleaned = []
    for item in results:
        url = item.get("link", "").strip()
        snippet = item.get("snippet", "").strip()
        
        if not url:
            continue
            
        # Basic filter: must be a valid X/Twitter URL
        if not is_valid_x_url(url):
            logger.info(f"Filtering out non-X URL: {url}")
            continue
            
        if not snippet:
            logger.warning(f"Snippet is empty for URL: {url}. Skipping.")
            continue
            
        cleaned.append({
            "url": url,
            "snippet": snippet
        })
        
    logger.info(f"Filtered results: {len(cleaned)} out of {len(results)} search results kept.")
    return cleaned


def log_to_google_sheet(cleaned_data: list, gcp_json: str, sheet_key: str, sheet_name: str, dry_run: bool):
    """
    Connects to the Google Sheet via service account authentication, reads existing URLs,
    deduplicates the scraped entries, and logs new ones.
    """
    if dry_run:
        logger.info("Dry-run: Simulating Google Sheets write operation.")
        logger.info(f"Would log {len(cleaned_data)} items if they don't already exist.")
        for i, item in enumerate(cleaned_data, 1):
            logger.info(f"  [{i}] Snippet: {item['snippet'][:60]}... | URL: {item['url']}")
        return
        
    # 1. Authenticate with Google Sheets API
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        service_account_info = json.loads(gcp_json)
        credentials = Credentials.from_service_account_info(
            service_account_info,
            scopes=scopes
        )
        gc = gspread.authorize(credentials)
    except Exception as e:
        logger.error(f"Failed to authenticate with Google Sheets API: {e}")
        logger.error("Verify that GCP_SERVICE_ACCOUNT_JSON matches your Service Account Key JSON.")
        sys.exit(1)
        
    # 2. Open the Target Spreadsheet
    try:
        if sheet_key:
            logger.info(f"Opening Google Sheet by unique ID: {sheet_key}")
            spreadsheet = gc.open_by_key(sheet_key)
        else:
            logger.info(f"Opening Google Sheet by Name: {sheet_name}")
            spreadsheet = gc.open(sheet_name)
    except gspread.exceptions.SpreadsheetNotFound:
        logger.error("Target spreadsheet not found.")
        logger.error("Please verify that the spreadsheet key/name is correct AND the Service Account email")
        logger.error(f"({service_account_info.get('client_email')}) is added to the Google Sheet as an 'Editor'.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to open Google Sheet: {e}")
        sys.exit(1)
        
    # 3. Read Worksheet & Check Headers
    worksheet = spreadsheet.get_worksheet(0)
    try:
        existing_rows = worksheet.get_all_values()
    except Exception as e:
        logger.error(f"Failed to read existing data from worksheet: {e}")
        sys.exit(1)
        
    existing_urls = set()
    
    # Initialize sheet with headers if it is completely empty
    if not existing_rows:
        headers = ["Complaint Snippet", "X/Twitter URL", "Date Logged"]
        try:
            worksheet.append_row(headers)
            logger.info("Worksheet was empty. Initialized with default columns.")
        except Exception as e:
            logger.error(f"Failed to write column headers to worksheet: {e}")
            sys.exit(1)
    else:
        # Collect existing URLs from the second column (Index 1) to avoid duplicates
        for row in existing_rows[1:]:  # Skip the header row
            if len(row) > 1:
                existing_urls.add(normalize_url(row[1]))
                
    # 4. Filter duplicates and prepare batch append
    rows_to_append = []
    batch_normalized_urls = set()
    current_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    for item in cleaned_data:
        url = item["url"]
        snippet = item["snippet"]
        normalized_url = normalize_url(url)
        
        # Check if URL already exists in Google Sheet
        if normalized_url in existing_urls:
            logger.info(f"Skipping duplicate URL already present in Google Sheet: {url}")
            continue
            
        # Avoid duplicate URLs within the current scraped batch itself
        if normalized_url in batch_normalized_urls:
            logger.info(f"Skipping duplicate URL within current scrape batch: {url}")
            continue
            
        rows_to_append.append([snippet, url, current_date])
        batch_normalized_urls.add(normalized_url)
        
    # 5. Write unique rows to Google Sheet in a single batch call (optimized API usage)
    if rows_to_append:
        try:
            worksheet.append_rows(rows_to_append)
            logger.info(f"Successfully appended {len(rows_to_append)} new unique complaints to the Google Sheet!")
        except Exception as e:
            logger.error(f"Failed to write data to Google Sheet: {e}")
            sys.exit(1)
    else:
        logger.info("No new unique complaints found in this run. Sheets are up to date!")


def main():
    logger.info("Initializing X Problem Scraper Pipeline...")
    
    # Load Environment Variables
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    serpapi_key = os.getenv("SERPAPI_KEY")
    gcp_json = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
    sheet_key = os.getenv("GOOGLE_SHEET_KEY")
    sheet_name = os.getenv("GOOGLE_SHEET_NAME")
    
    if dry_run:
        logger.info("=== RUNNING IN DRY RUN / SIMULATION MODE ===")
        logger.info("Variable Check:")
        logger.info(f"  SERPAPI_KEY: {'[PRESENT]' if serpapi_key else '[NOT PRESENT]'}")
        logger.info(f"  GCP_SERVICE_ACCOUNT_JSON: {'[PRESENT]' if gcp_json else '[NOT PRESENT]'}")
        logger.info(f"  GOOGLE_SHEET_KEY: {'[PRESENT]' if sheet_key else '[NOT PRESENT]'}")
        logger.info(f"  GOOGLE_SHEET_NAME: {'[PRESENT]' if sheet_name else '[NOT PRESENT]'}")
    else:
        # Validate required configs for a production run
        missing = []
        if not serpapi_key:
            missing.append("SERPAPI_KEY")
        if not gcp_json:
            missing.append("GCP_SERVICE_ACCOUNT_JSON")
        if not sheet_key and not sheet_name:
            missing.append("GOOGLE_SHEET_KEY (or GOOGLE_SHEET_NAME)")
            
        if missing:
            logger.error(f"Missing required environment secrets: {', '.join(missing)}")
            logger.error("Set the required environment variables or run in dry-run mode using: DRY_RUN=true")
            sys.exit(1)
            
    # Step 1: Fetch Search Results from SerpApi
    raw_results = fetch_serp_results(serpapi_key, dry_run)
    
    # Step 2: Clean and Filter data (drop non-X results)
    cleaned_data = clean_and_filter_results(raw_results)
    
    # Step 3: Log unique entries to Google Sheets
    log_to_google_sheet(
        cleaned_data=cleaned_data,
        gcp_json=gcp_json,
        sheet_key=sheet_key,
        sheet_name=sheet_name,
        dry_run=dry_run
    )
    
    logger.info("Scraper Pipeline finished execution.")


if __name__ == "__main__":
    main()
