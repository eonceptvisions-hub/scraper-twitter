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
import time
import json
import re
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

def get_or_create_query_history_sheet(spreadsheet) -> gspread.Worksheet:
    """
    Retrieves the 'Query History' worksheet, creating it with default headers if not found.
    """
    try:
        worksheet = spreadsheet.worksheet("Query History")
    except gspread.exceptions.WorksheetNotFound:
        # Create a 2-column sheet for Timestamp and Query
        worksheet = spreadsheet.add_worksheet(title="Query History", rows="1000", cols="2")
        worksheet.append_row(["Timestamp", "Generated Query"])
        logger.info("Created 'Query History' worksheet for rotation tracking.")
    return worksheet


def read_recent_queries(worksheet) -> list:
    """
    Reads the last 30 queries logged in the 'Query History' sheet.
    """
    try:
        rows = worksheet.get_all_values()
        recent_queries = []
        if len(rows) > 1:
            # Column B (index 1) contains the query, skip the header row (index 0)
            data_rows = rows[1:]
            # Grab the last 30 entries
            last_30_rows = data_rows[-30:]
            for row in last_30_rows:
                if len(row) > 1:
                    recent_queries.append(row[1].strip())
        return recent_queries
    except Exception as e:
        logger.error(f"Failed to read recent queries from history sheet: {e}")
        return []


def log_query(worksheet, query: str):
    """
    Appends a new query to the 'Query History' worksheet with the current UTC timestamp.
    """
    try:
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        worksheet.append_row([current_time, query])
        logger.info(f"Successfully logged new query to history: '{query}'")
    except Exception as e:
        logger.error(f"Failed to log query to history sheet: {e}")


def generate_daily_dork_query(client: genai.Client, recent_queries: list, model_name: str = "gemini-2.5-flash") -> str:
    """
    Asks Gemini to generate a creative, advanced Google Search dork query targeting x.com
    to find SaaS/business complaints, ensuring it does not repeat queries from the last 30 days.
    """
    recent_queries_str = "\n".join([f"- {q}" for q in recent_queries]) if recent_queries else "None"
    
    prompt = f"""
    You are an expert market dorking and OSINT engineer.
    Your job is to generate a single, highly effective Google Search dork query targeting x.com (Twitter) to find raw customer complaints, frustrations, and software tool desires.
    
    CRITICAL RESTRICTION: The new query must NOT be similar to or repeat any of these queries used in the last 30 days:
    {recent_queries_str}
    
    Guidelines:
    1. The query must start with 'site:x.com'.
    2. Focus on phrases indicating frustration, friction, or desire (e.g., "takes me hours to", "wish there was a tool", "why is it so hard to", "manual spreadsheet", "takes forever to").
    3. Target SaaS, software, startup, or business niches (e.g., marketing, finance, sales, operations, CRM, customer service, dev tools).
    4. MUST include negative keywords to filter out jobs, hiring, newsletters, courses, templates, ads, spam, and promotions. Examples: -job -hiring -recruiting -course -newsletter -sponsor -ad -giveaway -thread.
    5. Vary the niche (e.g., if recent queries targeted marketing or finance, target developer tooling, HR, legal, or customer support today).
    
    Output ONLY the query string, inside a code block or as plain text. Do not include quotes or any introductory/conversational text.
    """
    
    logger.info("Generating dynamic dork query via Gemini...")
    config = types.GenerateContentConfig(
        temperature=0.7 # higher temperature for creative query generation
    )
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config
    )
    
    query = response.text.strip()
    
    # Strip markdown code block wrappers if Gemini returns them
    if query.startswith("```"):
        lines = query.split("\n")
        if len(lines) > 2:
            query = "\n".join(lines[1:-1]).strip()
        else:
            query = query.replace("```", "").strip()
            
    # Remove surrounding double quotes if present
    if query.startswith('"') and query.endswith('"'):
        query = query[1:-1].strip()
        
    logger.info(f"Generated dynamic query: '{query}'")
    return query



def clean_google_snippet(snippet: str) -> str:
    """
    Cleans Google Search snippet texts by removing prepended dates, relative times (e.g. '3 days ago'),
    leading/trailing ellipses ('...' or '…'), and redundant whitespaces.
    """
    if not snippet:
        return ""
        
    # Match common Google date/time prefixes followed by ellipses
    # Examples:
    # - "May 12, 2024 ... "
    # - "3 days ago ... "
    # - "12h ago ... "
    # - "2024-05-12 ... "
    date_pattern = r"^((?:[A-Za-z]{3}\s+\d{1,2},\s+\d{4})|(?:\d{1,2}\s+[A-Za-z]{3,}\s+\d{4})|(?:\d+\s+(?:days?|hours?|mins?|minutes?|secs?|seconds?)\s+ago)|(?:\d{4}-\d{2}-\d{2})|(?:\d{2}/\d{2}/\d{4}))\s*(?:\.{3,}|…)\s*(.*)"
    prefix_match = re.match(date_pattern, snippet)
    if prefix_match:
        snippet = prefix_match.group(2)
    else:
        # Fallback to catch any short prefix (up to 30 characters) followed by ellipses
        # if it contains date indicators (digits, months, or 'ago')
        fallback_match = re.match(r"^(.{1,30}?)(?:\.{3,}|…)\s*(.*)", snippet)
        if fallback_match:
            prefix = fallback_match.group(1).lower()
            if any(char.isdigit() for char in prefix) or "ago" in prefix or any(m in prefix for m in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
                snippet = fallback_match.group(2)
                
    # Strip leading/trailing ellipses or dashes and spaces
    snippet = snippet.strip()
    if snippet.startswith("..."):
        snippet = snippet[3:]
    elif snippet.startswith("…"):
        snippet = snippet[1:]
        
    if snippet.endswith("..."):
        snippet = snippet[:-3]
    elif snippet.endswith("…"):
        snippet = snippet[:-1]
        
    return snippet.strip()


def fetch_serp_results(serpapi_key: str, query: str, dry_run: bool) -> list:
    """
    Queries SerpApi Google Search engine with a dynamically generated query.
    """
    if dry_run:
        logger.info("Dry-run: Simulating SerpApi search query.")
        return MOCK_ORGANIC_RESULTS
        
    logger.info(f"Querying SerpApi with: '{query}'")
    
    url = "https://serpapi.com/search"
    params = {
        "engine": "google",
        "q": query,
        "api_key": serpapi_key,
        "hl": "en",
        "gl": "us",
        "num": 100  # Pull up to 100 results to maximize efficiency per call
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if "error" in data:
            logger.error(f"SerpApi returned an error: {data['error']}")
            return []
            
        organic_results = data.get("organic_results", [])
        logger.info(f"SerpApi returned {len(organic_results)} organic results.")
        return organic_results
    except Exception as e:
        logger.error(f"Failed to query SerpApi: {e}")
        return []


def clean_and_filter_results(results: list) -> list:
    """
    Cleans search results by dropping entries without valid X/Twitter URLs,
    and strips dates and ellipses from text snippets.
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
            
        # Clean date prefixes and trailing ellipses
        cleaned_snippet = clean_google_snippet(snippet)
        if not cleaned_snippet:
            logger.warning(f"Snippet became empty after cleaning for URL: {url}. Skipping.")
            continue
            
        cleaned.append({
            "url": url,
            "snippet": cleaned_snippet
        })
        
    logger.info(f"Filtered results: {len(cleaned)} out of {len(results)} search results kept.")
    return cleaned


def analyze_with_gemini(snippet: str, client: genai.Client, model_name: str = "gemini-2.5-flash") -> BusinessConceptAnalysis:
    """
    Leverages Gemini in a split-call architecture to bypass tool/JSON incompatibility and rate limits:
    1. Research Stage: Call Gemini with Google Search tool to gather competitor and market insights.
    2. Sleep Throttle: Sleep 15 seconds to respect the 5 RPM rate limit.
    3. Structuring Stage: Call Gemini with Pydantic schema using the research summary to format the final JSON object.
    """
    # 1. Research Stage
    research_prompt = f"""
    You are an elite market researcher.
    
    We have extracted the following raw user pain point/complaint from X (Twitter):
    "{snippet}"
    
    Using Google Search, research the market to identify:
    1. If this is a genuine, solvable problem in the modern world.
    2. Direct competitors or existing SaaS tools that address this.
    3. The technical feasibility of building a solution.
    4. An unfair moat we could build to beat incumbents.
    
    Provide your analysis as a comprehensive, well-structured text summary.
    """
    
    logger.info("Step 1: Running Google Search Grounding research...")
    research_config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2
    )
    
    research_response = client.models.generate_content(
        model=model_name,
        contents=research_prompt,
        config=research_config
    )
    research_summary = research_response.text
    
    # Sleep to respect rate limits (5 RPM limit = 12 seconds minimum between requests)
    logger.info("Respecting rate limits: sleeping 15 seconds before structural analysis...")
    time.sleep(15)
    
    # 2. Structuring Stage
    structure_prompt = f"""
    You are an elite Venture Capitalist and SaaS Product Architect.
    
    Raw User Pain Point:
    "{snippet}"
    
    Market Research Summary:
    {research_summary}
    
    Based on this research, formulate a polished business concept. Output exactly matching the required JSON schema.
    """
    
    logger.info("Step 2: Structuring business concept with Pydantic schema...")
    structure_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=BusinessConceptAnalysis,
        temperature=0.2
    )
    
    structure_response = client.models.generate_content(
        model=model_name,
        contents=structure_prompt,
        config=structure_config
    )
    
    return structure_response.parsed


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


def format_google_sheet(spreadsheet):
    """
    Applies professional styling, freezes header rows, enables basic filtering, 
    and configures text wrapping and custom column dimensions for both worksheets.
    """
    logger.info("Applying premium formatting to Google Sheets...")
    try:
        # --- 1. Format Worksheet 1 (SaaS Incubation Sheet) ---
        worksheet_0 = spreadsheet.get_worksheet(0)
        sheet_id_0 = worksheet_0.id
        
        column_widths_0 = {
            0: 160, # Timestamp
            1: 280, # Raw Tweet
            2: 180, # X URL
            3: 100, # Feasibility
            4: 280, # Rationale
            5: 140, # Product Name
            6: 280, # Product Concept
            7: 220, # Core Features
            8: 160, # Tech Stack
            9: 180, # Competitors
            10: 280, # Unfair Moat
            11: 180  # Target Audience
        }
        
        requests_0 = [
            # Freeze the first row
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id_0,
                        "gridProperties": {
                            "frozenRowCount": 1
                        }
                    },
                    "fields": "gridProperties.frozenRowCount"
                }
            },
            # Set basic filter across headers
            {
                "setBasicFilter": {
                    "filter": {
                        "range": {
                            "sheetId": sheet_id_0,
                            "startRowIndex": 0,
                            "startColumnIndex": 0,
                            "endColumnIndex": 12
                        }
                    }
                }
            },
            # Style header row (sleek dark slate, white bold text, centered, font: Inter)
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id_0,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 12
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 0.098,  # #182232
                                "green": 0.137,
                                "blue": 0.200
                            },
                            "textFormat": {
                                "foregroundColor": {
                                    "red": 1.0,
                                    "green": 1.0,
                                    "blue": 1.0
                                },
                                "fontFamily": "Inter",
                                "fontSize": 11,
                                "bold": True
                            },
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "wrapStrategy": "WRAP"
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"
                }
            },
            # Style data cells (font: Inter, size: 10, vertical align: middle, text wrap: WRAP)
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id_0,
                        "startRowIndex": 1,
                        "endRowIndex": 1000,
                        "startColumnIndex": 0,
                        "endColumnIndex": 12
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {
                                "fontFamily": "Inter",
                                "fontSize": 10
                            },
                            "verticalAlignment": "MIDDLE",
                            "wrapStrategy": "WRAP"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,verticalAlignment,wrapStrategy)"
                }
            },
            # Center align and bold Feasibility score
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id_0,
                        "startRowIndex": 1,
                        "endRowIndex": 1000,
                        "startColumnIndex": 3,
                        "endColumnIndex": 4
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {
                                "bold": True
                            },
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat.bold,horizontalAlignment)"
                }
            },
            # Bold Product Name
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id_0,
                        "startRowIndex": 1,
                        "endRowIndex": 1000,
                        "startColumnIndex": 5,
                        "endColumnIndex": 6
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {
                                "bold": True
                            }
                        }
                    },
                    "fields": "userEnteredFormat(textFormat.bold)"
                }
            }
        ]
        
        # Add column widths to requests_0
        for col_idx, width in column_widths_0.items():
            requests_0.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id_0,
                        "dimension": "COLUMNS",
                        "startIndex": col_idx,
                        "endIndex": col_idx + 1
                    },
                    "properties": {
                        "pixelSize": width
                    },
                    "fields": "pixelSize"
                }
            })
            
        # Execute batch update for sheet 1
        spreadsheet.batch_update({"requests": requests_0})
        logger.info("Formatted primary SaaS Incubation worksheet.")
        
        # --- 2. Format Worksheet 2 (Query History Sheet) ---
        try:
            worksheet_1 = spreadsheet.worksheet("Query History")
            sheet_id_1 = worksheet_1.id
            
            column_widths_1 = {
                0: 160, # Timestamp
                1: 540  # Generated Query
            }
            
            requests_1 = [
                # Freeze first row
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id_1,
                            "gridProperties": {
                                "frozenRowCount": 1
                            }
                        },
                        "fields": "gridProperties.frozenRowCount"
                    }
                },
                # Set basic filter
                {
                    "setBasicFilter": {
                        "filter": {
                            "range": {
                                "sheetId": sheet_id_1,
                                "startRowIndex": 0,
                                "startColumnIndex": 0,
                                "endColumnIndex": 2
                            }
                        }
                    }
                },
                # Style header row (grey background, white bold text, centered)
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id_1,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": 2
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": {
                                    "red": 0.200,  # #334155
                                    "green": 0.255,
                                    "blue": 0.333
                                },
                                "textFormat": {
                                    "foregroundColor": {
                                        "red": 1.0,
                                        "green": 1.0,
                                        "blue": 1.0
                                    },
                                    "fontFamily": "Inter",
                                    "fontSize": 11,
                                    "bold": True
                                },
                                "horizontalAlignment": "CENTER",
                                "verticalAlignment": "MIDDLE",
                                "wrapStrategy": "WRAP"
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"
                    }
                },
                # Style data cells (font: Inter, size: 10, wrap: WRAP)
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id_1,
                            "startRowIndex": 1,
                            "endRowIndex": 1000,
                            "startColumnIndex": 0,
                            "endColumnIndex": 2
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {
                                    "fontFamily": "Inter",
                                    "fontSize": 10
                                },
                                "verticalAlignment": "MIDDLE",
                                "wrapStrategy": "WRAP"
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,verticalAlignment,wrapStrategy)"
                    }
                }
            ]
            
            # Add column widths to requests_1
            for col_idx, width in column_widths_1.items():
                requests_1.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id_1,
                            "dimension": "COLUMNS",
                            "startIndex": col_idx,
                            "endIndex": col_idx + 1
                        },
                        "properties": {
                            "pixelSize": width
                        },
                        "fields": "pixelSize"
                    }
                })
                
            spreadsheet.batch_update({"requests": requests_1})
            logger.info("Formatted 'Query History' worksheet.")
            
        except gspread.exceptions.WorksheetNotFound:
            pass
            
    except Exception as e:
        logger.error(f"Failed to apply Google Sheets formatting: {e}")


def main():
    logger.info("Initializing Autonomous AI SaaS Ideation Pipeline...")
    
    # Load Environment Variables
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    serpapi_key = os.getenv("SERPAPI_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    gcp_json = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
    sheet_key = os.getenv("GOOGLE_SHEET_KEY")
    max_items = int(os.getenv("MAX_ITEMS_PER_RUN", "5"))
    
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
            
    # Initialize Gemini Client if in production
    client = None
    if not dry_run:
        try:
            # The SDK automatically uses GEMINI_API_KEY from environment variables
            client = genai.Client()
        except Exception as e:
            logger.error(f"Failed to initialize Gemini Client: {e}")
            sys.exit(1)

    # Step 1: Open Target Google Sheet, Retrieve Existing URLs, and Query History
    existing_urls = set()
    worksheet = None
    recent_queries = []
    history_worksheet = None
    
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
                logger.info("Initialized Google Sheet with default columns.")
                
            # Access or create Query History sheet
            history_worksheet = get_or_create_query_history_sheet(spreadsheet)
            recent_queries = read_recent_queries(history_worksheet)
            
        except Exception as e:
            logger.error(f"Google Sheets setup failed: {e}")
            sys.exit(1)
            
    # Step 2: Generate dynamic dork query via Gemini (preventing repeats from last 30 days)
    if dry_run:
        daily_query = 'site:x.com ("takes me hours" OR "takes forever") (SaaS OR business) -job -hiring'
    else:
        daily_query = generate_daily_dork_query(client, recent_queries)
        log_query(history_worksheet, daily_query)
        logger.info("Sleeping 15 seconds after query generation to prevent rate limit issues...")
        time.sleep(15)

    # Step 3: Fetch search results from SerpApi using dynamically generated query
    raw_results = fetch_serp_results(serpapi_key, daily_query, dry_run)
    
    # Step 4: Filter & clean URLs
    cleaned_results = clean_and_filter_results(raw_results)
    
    # Step 5: Keep only unique, unprocessed entries
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
    
    # Slice the unprocessed list to stay under rate limits and save costs
    if len(new_unprocessed_items) > max_items:
        logger.info(f"Slicing list to first {max_items} items (configured via MAX_ITEMS_PER_RUN).")
        new_unprocessed_items = new_unprocessed_items[:max_items]
        
    # Step 6: Process through AI Brain
    new_rows_to_append = []
    
    if new_unprocessed_items:
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
                
                # Sleep to prevent overlap with the next item's first API call
                if i < len(new_unprocessed_items):
                    logger.info("Respecting rate limits: sleeping 15 seconds before processing next item...")
                    time.sleep(15)
                    
            except Exception as e:
                # Graceful try-except wrap around individual runs to prevent pipeline crash
                logger.error(f"Graceful Skip: Failed to analyze pain point from {url} due to error: {e}")
                # Even if it fails, sleep to reset rate limits before next loop
                if i < len(new_unprocessed_items):
                    time.sleep(15)
                continue
                
    # Step 7: Log unique results in a single optimized batch write
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
        
    # Format Google Sheet to look premium
    if not dry_run:
        format_google_sheet(spreadsheet)
        
    logger.info("SaaS Ideation & Research pipeline execution finished.")



if __name__ == "__main__":
    main()
