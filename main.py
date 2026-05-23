#!/usr/bin/env python3
"""
Autonomous AI SaaS Ideation & Research Pipeline
Queries SerpApi for X (Twitter) complaints, filters out duplicates,
processes them through a Gemini-based research agent with Search Grounding
and Pydantic validation, and logs structured business ideas to a Google Sheet.

Requirements:
- requests (for SerpApi)
- gspread (for Google Sheets)
- google-auth (for service account auth)
- google-genai (Gemini 2.5 Flash SDK)
- pydantic (Structured validation)
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
import urllib.parse
import requests
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Mock search results for dry-run mode
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


# --- Pydantic Schemas for Structured AI Outputs ---

class SaasProductConcept(BaseModel):
    name: str = Field(description="A catchy and creative name for the proposed SaaS product.")
    one_liner: str = Field(description="A concise one-liner summary of the SaaS product concept.")
    core_features: list[str] = Field(description="List of core MVP features, maximum 3.")
    mvp_stack_suggestion: str = Field(description="Suggested technology stack for building the MVP (e.g. React, Firebase, AI integrations).")


class MarketAnalysis(BaseModel):
    direct_competitors: list[str] = Field(description="List of direct competitors in the market.")
    our_unfair_moat: str = Field(description="Detailed analysis of how this concept beats the incumbents and what our unfair moat is.")


class BusinessConceptAnalysis(BaseModel):
    feasibility_score: int = Field(description="Feasibility and doability score of solving this in the modern world (1 to 10).")
    feasibility_rationale: str = Field(description="Exactly 2 sentences detailing technical or market viability friction and why it is or is not feasible.")
    saas_product_concept: SaasProductConcept = Field(description="The proposed SaaS product concept details.")
    market_analysis: MarketAnalysis = Field(description="Market and competitor analysis details.")
    target_audience: str = Field(description="The exact professional archetype / target audience willing to pay for this solution.")


# --- Helper Functions ---

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
        
        if netloc in ["twitter.com", "mobile.twitter.com", "mobile.x.com"]:
            netloc = "x.com"
            
        normalized = parsed._replace(netloc=netloc, scheme="https", fragment="").geturl()
        if normalized.endswith("/"):
            normalized = normalized[:-1]
        return normalized
    except Exception:
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


def get_mock_analysis(snippet: str) -> BusinessConceptAnalysis:
    """
    Generates high-fidelity mock Pydantic responses matching the mock search snippets.
    Used for safe, cost-effective local dry-run tests.
    """
    if "compile weekly status reports" in snippet:
        return BusinessConceptAnalysis(
            feasibility_score=8,
            feasibility_rationale="Highly feasible because weekly reports rely on structured data sources like Jira/GitHub APIs. The friction lies in building robust API integrations and custom layout templates.",
            saas_product_concept=SaasProductConcept(
                name="ReportFlow AI",
                one_liner="Automate client reporting by compiling Jira, GitHub, and Slack updates into gorgeous PDFs in one click.",
                core_features=[
                    "Multi-source API integrations",
                    "Custom drag-and-drop report layout builder",
                    "Scheduled automated email reports to clients"
                ],
                mvp_stack_suggestion="Next.js, TailwindCSS, Supabase, ReportLab (Python), OpenAI API"
            ),
            market_analysis=MarketAnalysis(
                direct_competitors=["ClickUp Reports", "Loom", "ReportGarden"],
                our_unfair_moat="Direct sync with developer commits and design updates to generate technical summaries without manual writing."
            ),
            target_audience="SaaS Product Managers & Agency Account Managers"
        )
    elif "RBAC and user permission" in snippet:
        return BusinessConceptAnalysis(
            feasibility_score=9,
            feasibility_rationale="Very feasible because authorization frameworks are common, but setting them up securely takes time. Developers will pay for ready-to-use SDKs that work with major frameworks.",
            saas_product_concept=SaasProductConcept(
                name="PermitLock",
                one_liner="Drop-in RBAC and fine-grained permissions SDK that deploys in under 5 minutes.",
                core_features=[
                    "Visual policy editor for non-technical admins",
                    "Multi-tenant support with real-time audit logs",
                    "Native SDKs for Node.js, Python, and Go"
                ],
                mvp_stack_suggestion="React, NestJS, PostgreSQL, Redis, Auth0"
            ),
            market_analysis=MarketAnalysis(
                direct_competitors=["Cerbos", "Permit.io", "Casbin"],
                our_unfair_moat="Instant schema generation from simple English system descriptions (e.g. 'Admins can delete, Editors can edit')."
            ),
            target_audience="Full-Stack Developers and SaaS Tech Leads"
        )
    else:
        return BusinessConceptAnalysis(
            feasibility_score=7,
            feasibility_rationale="Feasible but highly competitive design market makes customer acquisition expensive. The core challenge is building templates that look premium out of the box.",
            saas_product_concept=SaasProductConcept(
                name="AuraPromo",
                one_liner="AI design engine optimized to auto-generate weekly SaaS promotional assets from text descriptions.",
                core_features=[
                    "AI templates matching your branding colors",
                    "One-click asset resizing for all social networks",
                    "Direct scheduler integration for social posting"
                ],
                mvp_stack_suggestion="Vue.js, FastAPI, PostgreSQL, Cloudinary, Midjourney API"
            ),
            market_analysis=MarketAnalysis(
                direct_competitors=["Canva", "AdCreative.ai", "Figma"],
                our_unfair_moat="Specifically tuned models trained on top-converting SaaS product hunt launch graphics and dark-mode templates."
            ),
            target_audience="SaaS Solo Founders & Bootstrapped Marketers"
        )


# --- Core Pipeline Operations ---

def fetch_serp_results(serpapi_key: str, dry_run: bool) -> list:
    """
    Queries SerpApi Google Search engine with multiple queries to find target SaaS pain points.
    """
    if dry_run:
        logger.info("Dry-run: Simulating multiple SerpApi search queries.")
        return MOCK_ORGANIC_RESULTS
        
    queries = [
        'site:x.com "takes me hours to" SaaS',
        'site:x.com "wish there was a tool" business'
    ]
    
    all_results = []
    seen_links = set()
    
    for query in queries:
        logger.info(f"Querying SerpApi with query: '{query}'")
        url = "https://serpapi.com/search"
        params = {
            "engine": "google",
            "q": query,
            "api_key": serpapi_key,
            "hl": "en",
            "gl": "us",
            "num": 50  # 50 per query (max 100 total) to save search credits
        }
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if "error" in data:
                logger.error(f"SerpApi returned an error for query '{query}': {data['error']}")
                continue
                
            organic = data.get("organic_results", [])
            logger.info(f"SerpApi returned {len(organic)} results for query '{query}'.")
            
            for item in organic:
                link = item.get("link", "").strip()
                if link and link not in seen_links:
                    seen_links.add(link)
                    all_results.append(item)
                    
        except Exception as e:
            logger.error(f"Failed to fetch SerpApi results for query '{query}': {e}")
            continue
            
    logger.info(f"Total unique search results collected: {len(all_results)}")
    return all_results


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
            
        # Keep only valid X/Twitter links
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


def analyze_with_gemini(snippet: str, client: genai.Client, model_name: str = "gemini-2.5-flash") -> BusinessConceptAnalysis:
    """
    Leverages Gemini with Search Grounding to evaluate market feasibility and generate Pydantic outputs.
    """
    prompt = f"""
    You are an elite Venture Capitalist and SaaS Product Architect.
    
    We have extracted the following raw user pain point/complaint from X (Twitter):
    "{snippet}"
    
    Using your Google Search tool:
    1. Research the current market to determine if this is a genuine, unsolved problem in the modern world.
    2. Look up existing products, startups, or solutions that address this problem.
    3. Evaluate the technical and market feasibility of building a SaaS solution.
    4. Devise a polished SaaS product or feature concept to solve it, detailing its name, one-liner, core MVP stack, direct competitors, and our unfair moat.
    """
    
    # Configure request to enable Google Search grounding and enforce structured Pydantic schema
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        response_mime_type="application/json",
        response_schema=BusinessConceptAnalysis,
        temperature=0.2, # low temperature for high reliability in analysis
    )
    
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config
    )
    
    return response.parsed


def map_analysis_to_row(raw_tweet: str, url: str, analysis: BusinessConceptAnalysis) -> list:
    """
    Flats a BusinessConceptAnalysis Pydantic model into a structured Google Sheets row.
    """
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # Map sub-fields
    features_str = ", ".join(analysis.saas_product_concept.core_features)
    competitors_str = ", ".join(analysis.market_analysis.direct_competitors)
    
    row = [
        current_time,
        raw_tweet,
        url,
        analysis.feasibility_score,
        analysis.feasibility_rationale,
        analysis.saas_product_concept.name,
        analysis.saas_product_concept.one_liner,
        features_str,
        analysis.saas_product_concept.mvp_stack_suggestion,
        competitors_str,
        analysis.market_analysis.our_unfair_moat,
        analysis.target_audience
    ]
    return row


def main():
    logger.info("Initializing Autonomous AI SaaS Ideation Pipeline...")
    
    # Load Environment Variables
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    serpapi_key = os.getenv("SERPAPI_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    gcp_json = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
    sheet_key = os.getenv("GOOGLE_SHEET_KEY")
    
    if dry_run:
        logger.info("=== RUNNING IN DRY RUN / SIMULATION MODE ===")
    else:
        # Validate configs for production run
        missing = []
        if not serpapi_key:
            missing.append("SERPAPI_KEY")
        if not gemini_key:
            missing.append("GEMINI_API_KEY")
        if not gcp_json:
            missing.append("GCP_SERVICE_ACCOUNT_JSON")
        if not sheet_key:
            missing.append("GOOGLE_SHEET_KEY")
            
        if missing:
            logger.error(f"Missing required environment secrets: {', '.join(missing)}")
            logger.error("Set the required environment variables or run in dry-run mode using: DRY_RUN=true")
            sys.exit(1)
            
    # Step 1: Open Target Google Sheet & Retrieve Existing URLs (Deduplication Check)
    existing_urls = set()
    worksheet = None
    
    if not dry_run:
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
            spreadsheet = gc.open_by_key(sheet_key)
            worksheet = spreadsheet.get_worksheet(0)
            existing_rows = worksheet.get_all_values()
            
            # If sheet has values, collect URLs from Column C (index 2) to skip duplicates
            if existing_rows:
                for row in existing_rows[1:]: # skip header row
                    if len(row) > 2:
                        existing_urls.add(normalize_url(row[2]))
            else:
                # Completely empty sheet: write headers first
                headers = [
                    "Timestamp", "Raw Tweet", "X URL", "Feasibility (1-10)", "Rationale",
                    "Product Name", "Product Concept", "Core Features", "Tech Stack",
                    "Competitors", "Unfair Moat", "Target Audience"
                ]
                worksheet.append_row(headers)
                logger.info("Initialized Google Sheet with default incubation columns.")
                
        except Exception as e:
            logger.error(f"Google Sheets setup failed: {e}")
            sys.exit(1)
            
    # Step 2: Fetch search results from SerpApi (supporting multiple queries)
    raw_results = fetch_serp_results(serpapi_key, dry_run)
    
    # Step 3: Filter & clean URLs
    cleaned_results = clean_and_filter_results(raw_results)
    
    # Step 4: Keep only unique, unprocessed entries
    new_unprocessed_items = []
    seen_in_batch = set()
    
    for item in cleaned_results:
        url = item["url"]
        norm_url = normalize_url(url)
        
        if norm_url in existing_urls:
            logger.info(f"Skipping duplicate URL already present in Google Sheet: {url}")
            continue
            
        if norm_url in seen_in_batch:
            continue
            
        seen_in_batch.add(norm_url)
        new_unprocessed_items.append(item)
        
    logger.info(f"Found {len(new_unprocessed_items)} new unique inputs to evaluate.")
    
    # Step 5: Process through AI Brain
    new_rows_to_append = []
    
    if new_unprocessed_items:
        # Initialize Gemini Client if in production
        client = None
        if not dry_run:
            try:
                # The SDK automatically uses GEMINI_API_KEY from environment variables
                client = genai.Client()
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Client: {e}")
                sys.exit(1)
                
        for i, item in enumerate(new_unprocessed_items, 1):
            snippet = item["snippet"]
            url = item["url"]
            logger.info(f"Processing pain point [{i}/{len(new_unprocessed_items)}]: {url}")
            
            try:
                if dry_run:
                    analysis = get_mock_analysis(snippet)
                else:
                    analysis = analyze_with_gemini(snippet, client)
                    
                row_data = map_analysis_to_row(snippet, url, analysis)
                new_rows_to_append.append(row_data)
                logger.info(f"Analysis successful for {url}. Concept Generated: '{analysis.saas_product_concept.name}'")
                
            except Exception as e:
                # Graceful try-except wrap around individual runs to prevent pipeline crash
                logger.error(f"Graceful Skip: Failed to analyze pain point from {url} due to error: {e}")
                continue
                
    # Step 6: Log unique results in a single optimized batch write
    if new_rows_to_append:
        if dry_run:
            logger.info("Dry-run: Simulating writing row data to Google Sheets.")
            logger.info(f"Rows proposed for log: {len(new_rows_to_append)}")
            for i, row in enumerate(new_rows_to_append, 1):
                logger.info(f"Row {i}: Name='{row[5]}' | Concept='{row[6]}' | Feasibility={row[3]} | URL={row[2]}")
        else:
            try:
                worksheet.append_rows(new_rows_to_append)
                logger.info(f"Successfully logged {len(new_rows_to_append)} validated business concepts to the Google Sheet!")
            except Exception as e:
                logger.error(f"Failed to write results to Google Sheet: {e}")
                sys.exit(1)
    else:
        logger.info("No new unique concepts were generated. Sheets are up to date!")
        
    logger.info("SaaS Ideation & Research pipeline execution finished.")


if __name__ == "__main__":
    main()
