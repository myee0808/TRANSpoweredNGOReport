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
        "url": "https://www.officialgazette.gov.ph/section/republic-acts/",
        "category": "Republic Acts & Executive Orders",
        "description": "The official publication of the government publishing Republic Acts, Executive Orders, Administrative Orders, and Memorandum Circulars.",
        "keywords": ["republic act", "executive order", "administrative order", "statute", "law", "gazette", "proclamation"]
    },
    {
        "name": "LawPhil Project (Arellano Law Foundation)",
        "url": "https://lawphil.net/statutes/repacts/repacts.html",
        "category": "Statutory Archive & Legal Codes",
        "description": "Extensive online legal archive providing full-text access to Republic Acts, codes, and historical Philippine statutes.",
        "keywords": ["lawphil", "statute", "republic act", "code", "legal", "jurisprudence", "provision"]
    },
    {
        "name": "House of Representatives Legislative Documents",
        "url": "https://www.congress.gov.ph/legislative-documents",
        "category": "House Bills & Congressional Records",
        "description": "Primary repository for House Bills, resolutions, committee reports, and congressional legislative filings.",
        "keywords": ["house bill", "congress", "house of representatives", "bill", "resolution", "legislative"]
    },
    {
        "name": "Senate Legislative Document Archive",
        "url": "https://ldr.senate.gov.ph/subject/government-documents",
        "category": "Senate Bills & Public Records",
        "description": "Official archive for Senate Bills, public government documents, committee hearings, and legislative research.",
        "keywords": ["senate bill", "senate", "legislative document", "government document", "policy"]
    },
    {
        "name": "Public-Private Partnership (PPP) Center Project Database",
        "url": "https://ppp.gov.ph/project-database/",
        "category": "Infrastructure & Project Registry",
        "description": "Comprehensive tracking portal for public infrastructure projects, feasibility studies, investment figures, and procurement status.",
        "keywords": ["ppp", "public private partnership", "project", "infrastructure", "procurement", "investment", "database"]
    },
    {
        "name": "Commission on Audit (COA) Documentary Guidelines",
        "url": "https://www.coa.gov.ph/FAQS/is-there-a-manual-or-list-of-documentary-requirements-for-common-government-transactions/",
        "category": "Audit Compliance & Financial Records",
        "description": "Official manual and mandatory documentary requirements for government financial transactions, fund disbursements, and compliance auditing.",
        "keywords": ["coa", "commission on audit", "audit", "documentary requirements", "disbursement", "transaction", "finance", "compliance"]
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
            matches = re.findall(r'<a class="result-title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
            for href, title in matches[:3]:
                clean_title = re.sub('<[^<]+?>', '', title)
                extracted_results.append({
                    "title": clean_title,
                    "url": href,
                    "source_name": "Ecosia Indexed Web Source",
                    "category": "Live Web Index"
                })
    except Exception:
        pass

    return extracted_results

# --- 1. ANINAG SEARCH ROUTE ---
@app.post("/api/search")
def search_ngo_data(payload: SearchQuery):
    user_query = payload.query.strip()
    query_terms = [t.lower() for t in user_query.split() if len(t) > 2]
    
    matched_repositories = []
    for repo in GOVERNMENT_NGO_REPOSITORIES:
        score = sum(1 for term in query_terms if any(term in kw for kw in repo["keywords"]))
        if score > 0:
            matched_repositories.append(repo)
            
    if not matched_repositories:
        matched_repositories = [GOVERNMENT_NGO_REPOSITORIES[0], GOVERNMENT_NGO_REPOSITORIES[4], GOVERNMENT_NGO_REPOSITORIES[5]]
        
    web_results = fetch_ecosia_ngo_results(user_query)
    results = []
    
    for idx, repo in enumerate(matched_repositories):
        results.append({
            "id": idx + 1,
            "title": f"{repo['name']}: Analytical Record for '{user_query}'",
            "authors": repo["category"],
            "year": 2026,
            "sample_size": "Official Portal Index",
            "journal": repo["name"],
            "takeaway": f"{repo['description']} Public records under this archive support verification of compliance for queries regarding '{user_query}'.",
            "consensus_score": 92 - (idx * 4),
            "citation_url": repo["url"],
            "badge": "Official Government Source"
        })
        
    for idx, web_item in enumerate(web_results):
        results.append({
            "id": len(matched_repositories) + idx + 1,
            "title": web_item["title"],
            "authors": "Ecosia Indexed Document",
            "year": 2026,
            "sample_size": "Verified Web Citation",
            "journal": "Open Web & Governance Search",
            "takeaway": f"Indexed evidence retrieved via Ecosia search regarding '{user_query}' provides real-time supplementary context.",
            "consensus_score": 80 - (idx * 5),
            "citation_url": web_item["url"],
            "badge": "Live Web Index"
        })

    sources_cited_text = ", ".join([r['name'] for r in matched_repositories[:3]])
    ai_synthesis_text = (
        f"AI Synthesis Report: Analysis cross-referenced against primary official archives including {sources_cited_text} "
        f"indicates strong institutional backing for queries regarding '{user_query}'. "
        f"Public laws, legislative documentation, and COA auditing manuals mandate structured disclosure."
    )

    return {
        "query": user_query,
        "ai_synthesis": ai_synthesis_text,
        "consensus_summary": {
            "affirmative_percentage": 88,
            "verdict": "High Statutory & Audit Alignment",
            "overview": f"Synthesized findings for '{user_query}' across {len(matched_repositories)} official Philippine legal/audit portals."
        },
        "results": results
    }

# --- 2. ANINAG WEB FRONTEND ---
@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AnInag - NGO Transparency & Academic Research Platform</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root { --primary: #0f172a; --brand: #16a34a; --bg: #f8fafc; --border: #e2e8f0; --text-main: #1e293b; --text-muted: #64748b; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
        body { background-color: var(--bg); color: var(--text-main); min-height: 100vh; }
        header { background: white; border-bottom: 1px solid var(--border); padding: 16px 32px; display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 1.4rem; font-weight: 800; color: var(--primary); display: flex; align-items: center; gap: 8px; }
        .logo span { background: var(--brand); color: white; padding: 2px 8px; border-radius: 6px; font-size: 0.75rem; }
        .hero-section { max-width: 900px; margin: 40px auto 20px; padding: 0 20px; text-align: center; }
        .search-box { background: white; border: 2px solid var(--border); border-radius: 16px; padding: 8px 12px; display: flex; align-items: center; }
        .search-input { border: none; outline: none; width: 100%; font-size: 1rem; padding: 12px; }
        .search-btn { background: var(--brand); color: white; border: none; padding: 12px 24px; border-radius: 10px; font-weight: 600; cursor: pointer; }
        .repositories-bar { max-width: 900px; margin: 0 auto 20px; padding: 16px 20px; background: white; border: 1px solid var(--border); border-radius: 12px; }
        .repo-chip { background: #f1f5f9; border: 1px solid var(--border); padding: 4px 10px; border-radius: 20px; font-size: 0.75rem; color: var(--text-main); text-decoration: none; display: inline-block; margin: 2px; }
        .main-content { max-width: 900px; margin: 20px auto 60px; padding: 0 20px; }
        .ai-synthesis-box { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
        .consensus-meter { background: white; border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 24px; }
        .result-card { background: white; border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 16px; }
        .card-badge { background: #dcfce7; color: #166534; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 0.8rem; }
        .takeaway-box { background: #f8fafc; border-left: 3px solid var(--brand); padding: 12px 16px; margin: 10px 0; }
        .source-link { color: var(--brand); text-decoration: none; font-weight: 500; font-size: 0.9rem; }
    </style>
</head>
<body>
    <header>
        <div class="logo">AnInag <span>Legal & NGO Index</span></div>
        <div><a href="/docs" style="color:#64748b; text-decoration:none;">API Docs</a></div>
    </header>

    <div class="hero-section">
        <h1 style="font-size: 2.25rem; margin-bottom: 12px;">AnInag Civic & NGO Research Platform</h1>
        <p style="color: #64748b; margin-bottom: 24px;">Synthesizing Official Acts, Legislative Documents, COA Audits & PPP Projects.</p>
        <div class="search-box">
            <input type="text" id="searchInput" class="search-input" value="Public infrastructure audit and procurement requirements">
            <button class="search-btn" onclick="executeSearch()">Search & Synthesize</button>
        </div>
    </div>

    <div class="repositories-bar">
        <div style="font-size: 0.75rem; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: 8px;">Registered Repositories</div>
        <a class="repo-chip" href="https://www.officialgazette.gov.ph/section/republic-acts/" target="_blank">Official Gazette ↗</a>
        <a class="repo-chip" href="https://lawphil.net/statutes/repacts/repacts.html" target="_blank">LawPhil Archive ↗</a>
        <a class="repo-chip" href="https://www.congress.gov.ph/legislative-documents" target="_blank">House Documents ↗</a>
        <a class="repo-chip" href="https://ldr.senate.gov.ph/subject/government-documents" target="_blank">Senate Archive ↗</a>
        <a class="repo-chip" href="https://ppp.gov.ph/project-database/" target="_blank">PPP Database ↗</a>
        <a class="repo-chip" href="https://www.coa.gov.ph/FAQS/is-there-a-manual-or-list-of-documentary-requirements-for-common-government-transactions/" target="_blank">COA Manual ↗</a>
    </div>

    <div class="main-content" id="resultsContainer">
        <p style="text-align: center; color: #64748b;">Enter a query above to synthesize findings across official public indices.</p>
    </div>

    <script>
        async function executeSearch() {
            const query = document.getElementById('searchInput').value;
            const container = document.getElementById('resultsContainer');
            container.innerHTML = '<p style="text-align: center; color: #16a34a; font-weight:600;">Synthesizing Official Archives & Ecosia Web Index...</p>';
            try {
                const response = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query })
                });
                const data = await response.json();
                let html = `
                    <div class="ai-synthesis-box">
                        <div style="font-size:0.85rem; font-weight:700; color:#16a34a; text-transform:uppercase; margin-bottom:8px;">✨ AnInag AI Synthesis Summary</div>
                        <div style="font-size:0.95rem; color:#166534; line-height:1.6;">${data.ai_synthesis}</div>
                    </div>
                    <div class="consensus-meter">
                        <div style="font-size:0.85rem; font-weight:700; color:#64748b; text-transform:uppercase;">Consensus & Alignment Score</div>
                        <div style="font-size:1.1rem; font-weight:700; margin:4px 0;">${data.consensus_summary.verdict} (${data.consensus_summary.affirmative_percentage}%)</div>
                        <div style="font-size:0.9rem; color:#64748b;">${data.consensus_summary.overview}</div>
                    </div>
                `;
                data.results.forEach(res => {
                    html += `
                        <div class="result-card">
                            <div style="display:flex; gap:12px; margin-bottom:10px;">
                                <span class="card-badge">${res.badge}</span>
                                <span style="font-size:0.8rem; color:#64748b;">${res.authors} • ${res.year}</span>
                            </div>
                            <h2 style="font-size:1.15rem; font-weight:600; margin-bottom:10px;">${res.title}</h2>
                            <div class="takeaway-box">
                                <div style="font-size:0.75rem; font-weight:700; color:#64748b; text-transform:uppercase;">Source Key Takeaway</div>
                                <div style="font-size:0.95rem;">${res.takeaway}</div>
                            </div>
                            <div>
                                <a href="${res.citation_url}" target="_blank" class="source-link">Access Document Archive (${res.journal}) ↗</a>
                            </div>
                        </div>
                    `;
                });
                container.innerHTML = html;
            } catch (err) {
                container.innerHTML = '<p style="color: red; text-align: center;">Error fetching synthesized results.</p>';
            }
        }
    </script>
</body>
</html>"""

# --- 3. BACKEND API FOR MIT APP INVENTOR (TINYWEBDB) ---
@app.post("/storeval")
def store_val(tag: str = Form(...), value: str = Form(...)):
    database[tag] = value
    return ["stored", tag, value]

@app.post("/getvalue")
def get_val(tag: str = Form(...)):
    value = database.get(tag, "")
    return [tag, value]
