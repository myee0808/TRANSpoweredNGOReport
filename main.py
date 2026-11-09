from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from typing import Dict, List, Optional
from pydantic import BaseModel
import urllib.request
import urllib.parse
import re

app = FastAPI(title="AnInag NGO Transparency & Research Backend")

# In-memory storage for App Inventor TinyWebDB
database: Dict[str, str] = {}

# --- OFFICIAL GOVERNMENT & NGO DOCUMENT REPOSITORIES ---
GOVERNMENT_NGO_REPOSITORIES = [
    {
        "name": "Official Gazette of the Philippines",
        "url": "https://www.officialgazette.gov.ph/",
        "search_url_template": "https://www.officialgazette.gov.ph/?s={query}",
        "category": "Republic Acts & Executive Orders",
        "description": "Official government publication for Republic Acts, Executive Orders, and Memorandum Circulars.",
        "keywords": ["republic act", "executive order", "administrative order", "statute", "law", "gazette", "proclamation", "confidential funds"]
    },
    {
        "name": "LawPhil Project (Arellano Law Foundation)",
        "url": "https://lawphil.net/statutes/repacts/repacts.html",
        "search_url_template": "https://www.google.com/search?q=site:lawphil.net+{query}",
        "category": "Statutory Archive & Legal Codes",
        "description": "Extensive online legal archive providing full-text access to Republic Acts, codes, and historical statutes.",
        "keywords": ["lawphil", "statute", "republic act", "code", "legal", "jurisprudence", "provision", "confidential"]
    },
    {
        "name": "House of Representatives Legislative Documents",
        "url": "https://www.congress.gov.ph/legislative-documents",
        "search_url_template": "https://www.congress.gov.ph/legislative-documents/?q={query}",
        "category": "House Bills & Congressional Records",
        "description": "Repository for House Bills, resolutions, committee reports, and congressional filings.",
        "keywords": ["house bill", "congress", "house of representatives", "bill", "resolution", "legislative", "appropriations"]
    },
    {
        "name": "Senate Legislative Document Archive",
        "url": "https://ldr.senate.gov.ph/subject/government-documents",
        "search_url_template": "https://ldr.senate.gov.ph/subject/government-documents?search={query}",
        "category": "Senate Bills & Public Records",
        "description": "Official archive for Senate Bills, public government documents, committee hearings, and legislative research.",
        "keywords": ["senate bill", "senate", "legislative document", "government document", "policy", "budget"]
    },
    {
        "name": "Public-Private Partnership (PPP) Center Database",
        "url": "https://ppp.gov.ph/project-database/",
        "search_url_template": "https://ppp.gov.ph/?s={query}",
        "category": "Infrastructure & Project Registry",
        "description": "Tracking portal for public infrastructure projects, feasibility studies, and procurement status.",
        "keywords": ["ppp", "public private partnership", "project", "infrastructure", "procurement", "investment", "database"]
    },
    {
        "name": "Commission on Audit (COA) Documentary Guidelines",
        "url": "https://www.coa.gov.ph/FAQS/is-there-a-manual-or-list-of-documentary-requirements-for-common-government-transactions/",
        "search_url_template": "https://www.coa.gov.ph/?s={query}",
        "category": "Audit Compliance & Financial Records",
        "description": "Mandatory documentary requirements for government financial transactions, fund disbursements, and compliance auditing.",
        "keywords": ["coa", "commission on audit", "audit", "documentary requirements", "disbursement", "transaction", "finance", "compliance", "confidential funds"]
    }
]

class SearchQuery(BaseModel):
    query: str
    sources: Optional[List[str]] = None

def fetch_ecosia_ngo_results(query: str) -> List[Dict]:
    encoded_query = urllib.parse.quote(f"{query} site:gov.ph OR site:org OR site:lawphil.net OR NGO report")
    url = f"https://html.ecosia.org/search?q={encoded_query}"
    
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    
    extracted_results = []
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            html = response.read().decode('utf-8')
            matches = re.findall(r'<a class="result-title
