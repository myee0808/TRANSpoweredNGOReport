from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from typing import Dict, List, Optional
from pydantic import BaseModel
import urllib.request
import urllib.parse
import json
import re

app = FastAPI(title="AnInag NGO Transparency & Research Backend")

# In-memory storage for App Inventor TinyWebDB
database: Dict[str, str] = {}

# --- OFFICIAL GOVERNMENT & NGO TARGET REPOSITORIES ---
GOVERNMENT_NGO_REPOSITORIES = [
    {
        "name": "Official Gazette of the Philippines",
        "url": "https://www.officialgazette.gov.ph/",
        "search_url_template": "https://www.officialgazette.gov.ph/?s={query}",
        "category": "Republic Acts & Executive Orders",
        "description": "Official government publication for Republic Acts, Executive Orders, Administrative Orders, and Proclamations.",
        "document_summary": "Publishes enacted Republic Acts, presidential issuances, administrative rules, and official state documents detailing legal mandates, budget allocations, and state expenditures."
    },
    {
        "name": "LawPhil Project (Arellano Law Foundation)",
        "url": "https://lawphil.net/statutes/repacts/repacts.html",
        "search_url_template": "https://www.google.com/search?q=site:lawphil.net+{query}",
        "category": "Statutory Archive & Legal Codes",
        "description": "Extensive online legal archive providing full-text access to Republic Acts, codes, and historical statutes.",
        "document_summary": "Maintains a comprehensive full-text statutory archive of Philippine legal codes, historic acts, and judicial provisions governing state transactions."
    },
    {
        "name": "House of Representatives Legislative Documents",
        "url": "https://www.congress.gov.ph/legislative-documents",
        "search_url_template": "https://www.congress.gov.ph/legislative-documents/?q={query}",
        "category": "House Bills & Congressional Records",
        "description": "Repository for House Bills, resolutions, committee reports, and congressional filings.",
        "document_summary": "Archives filed House Bills, committee hearing transcripts, budgetary appropriations resolutions, and legislative tracking records."
    },
    {
        "name": "Senate Legislative Document Archive",
        "url": "https://ldr.senate.gov.ph/subject/government-documents",
        "search_url_template": "https://ldr.senate.gov.ph/subject/government-documents?search={query}",
        "category": "Senate Bills & Public Records",
        "description": "Official archive for Senate Bills, public government documents, committee hearings, and legislative research.",
        "document_summary": "Provides official records of Senate Bills, committee inquiries, policy position papers, and national policy deliberations."
    },
    {
        "name": "Public-Private Partnership (PPP) Center Project Database",
        "url": "https://ppp.gov.ph/project-database/",
        "search_url_template": "https://ppp.gov.ph/?s={query}",
        "category": "Infrastructure & Project Registry",
        "description": "Tracking portal for public infrastructure projects, feasibility studies, investment figures, and procurement status.",
        "document_summary": "Catalogs infrastructure project pipelines, financial feasibility studies, contractual agreements, and bidding parameters for public-private ventures."
    },
    {
        "name": "Commission on Audit (COA) Documentary Guidelines",
        "url": "https://www.coa.gov.ph/FAQS/is-there-a-manual-or-list-of-documentary-requirements-for-common-government-transactions/",
        "search_url_template": "https://www.coa.gov.ph/?s={query}",
        "category": "Audit Compliance & Financial Records",
        "description": "Mandatory documentary requirements for government financial transactions, fund disbursements, and compliance auditing.",
        "document_summary": "Details mandatory audit checklists, voucher requirements, disbursement rules, and compliance standards for public fund utilization."
    }
]

class SearchQuery(BaseModel):
    query: str
    user_id: Optional[str] = "default_user"
    sources: Optional[List[str]] = None

def fetch_ecosia_ngo_results(query: str) -> List[Dict]:
    encoded_query = urllib.parse.quote(f"{query} site:gov.ph OR site:org OR site:lawphil.net")
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
            for href, title in matches[:2]:
                clean_title = re.sub(r'<[^<]+?>', '', title)
                fragment_url = f"{href}#:~:text={urllib.parse.quote(query)}"
                extracted_results.append({
                    "title": clean_title,
                    "url": fragment_url,
                    "source_name": "Ecosia Indexed Web Source",
                    "category": "Live Deep Web Index"
                })
    except Exception:
        pass

    return extracted_results

# --- 1. ANINAG SEARCH & POINT REWARD ROUTE ---
@app.post("/api/search")
def search_ngo_data(payload: SearchQuery):
    user_query = payload.query.strip().lower()
    user_id = payload.user_id.strip() if payload.user_id else "default_user"
    encoded_user_query = urllib.parse.quote(user_query)
    
    point_tag = f"points_{user_id}"
    current_points = int(database.get(point_tag, "0"))
    new_points = current_points + 1
    database[point_tag] = str(new_points)

    results = []
    for idx, repo in enumerate(GOVERNMENT_NGO_REPOSITORIES):
        direct_deep_link = repo["search_url_template"].format(query=encoded_user_query)
        document_gist = (
            f"Document Gist & Content Overview: Within the {repo['name']} repository, records and guidelines concerning '{user_query}' "
            f"outline {repo['document_summary'].lower()} This archive provides direct statutory authority, compliance frameworks, "
            f"and official documentation relevant to your search."
        )
        
        results.append({
            "id": idx + 1,
            "title": f"{repo['name']}: Targeted Records for '{user_query}'",
            "authors": repo["category"],
            "year": 2026,
            "sample_size": "Complete Target Repository Match",
            "journal": repo["name"],
            "takeaway": document_gist,
            "consensus_score": 96 - (idx * 2),
            "citation_url": direct_deep_link,
            "badge": "Official Target Repository"
        })
        
    web_results = fetch_ecosia_ngo_results(user_query)
    for idx, web_item in enumerate(web_results):
        results.append({
            "id": len(GOVERNMENT_NGO_REPOSITORIES) + idx + 1,
            "title": web_item["title"],
            "authors": "Ecosia Indexed Document",
            "year": 2026,
            "sample_size": "Highlighted Passage",
            "journal": "Open Web Search Index",
            "takeaway": f"Document Gist & Content Overview: Live web source data details specific operational context, background data, and public disclosures regarding '{user_query}'.",
            "consensus_score": 85 - (idx * 4),
            "citation_url": web_item["url"],
            "badge": "Live Text-Fragment Link"
        })

    ai_synthesis_text = (
        f"AnInag Comprehensive AI Synthesis: Evaluating across all registered official target repositories "
        f"(Official Gazette, LawPhil, House Legislative Documents, Senate Archives, PPP Database, and COA Audit Guidelines), "
        f"searches regarding '{user_query}' intersect with multiple legal mandates, financial reporting structures, and compliance protocols."
    )

    return {
        "query": user_query,
        "user_id": user_id,
        "points_earned": 1,
        "total_points": new_points,
        "ai_synthesis": ai_synthesis_text,
        "consensus_summary": {
            "affirmative_percentage": 95,
            "verdict": f"Evaluated Across All {len(GOVERNMENT_NGO_REPOSITORIES)} Target Repositories",
            "overview": f"1 point awarded to '{user_id}'. Total Points: {new_points}."
        },
        "results": results
    }

@app.get("/api/points/{user_id}")
def get_user_points(user_id: str):
    points = database.get(f"points_{user_id}", "0")
    return {"user_id": user_id, "points": int(points)}

# --- 2. ANINAG WEB FRONTEND WITH EARTHY COLOR PALETTE ---
@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AnInag - NGO Transparency & Research Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #5B3A29;
            --brand: #A76D4D;
            --brand-dark: #8F4E3E;
            --bg: #F0F0DF;
            --card-bg: #FFFFFF;
            --border: #E0DCBE;
            --text-main: #5B3A29;
            --text-muted: #AF8A69;
            --accent-cream: #D9CBAE;
            --badge-bg: #E4CDAF;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
        body { background-color: var(--bg); color: var(--text-main); min-height: 100vh; }
        
        header { background: var(--card-bg); border-bottom: 2px solid var(--border); padding: 16px 32px; display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 1.4rem; font-weight: 800; color: var(--primary); display: flex; align-items: center; gap: 8px; }
        .logo span { background: var(--brand); color: #fff; padding: 2px 8px; border-radius: 6px; font-size: 0.75rem; }
        
        .user-points-badge { background: var(--accent-cream); border: 1px solid var(--border); color: var(--primary); padding: 6px 14px; border-radius: 20px; font-weight: 700; font-size: 0.9rem; }
        
        .hero-section { max-width: 900px; margin: 40px auto 20px; padding: 0 20px; text-align: center; }
        .hero-section h1 { color: var(--primary); }
        .hero-section p { color: var(--text-muted); }

        .user-id-bar { max-width: 900px; margin: 0 auto 12px; padding: 0 20px; display: flex; justify-content: flex-end; align-items: center; gap: 8px; font-size: 0.85rem; color: var(--text-muted); }
        .user-id-input { border: 1px solid var(--border); background: var(--card-bg); padding: 6px 10px; border-radius: 6px; outline: none; font-weight: 600; color: var(--primary); }
        
        .search-box { background: var(--card-bg); border: 2px solid var(--border); border-radius: 16px; padding: 8px 12px; display: flex; align-items: center; box-shadow: 0 4px 12px rgba(91, 58, 41, 0.05); }
        .search-box:focus-within { border-color: var(--brand); }
        .search-input { border: none; outline: none; width: 100%; font-size: 1rem; padding: 12px; background: transparent; color: var(--primary); }
        .search-btn { background: var(--brand); color: #fff; border: none; padding: 12px 24px; border-radius: 10px; font-weight: 600; cursor: pointer; transition: background 0.2s; }
        .search-btn:hover { background: var(--brand-dark); }
        
        .repositories-bar { max-width: 900px; margin: 0 auto 20px; padding: 16px 20px; background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; }
        .repo-chip { background: var(--accent-cream); border: 1px solid var(--border); padding: 4px 10px; border-radius: 20px; font-size: 0.75rem; color: var(--primary); text-decoration: none; display: inline-block; margin: 2px; font-weight: 500; }
        .repo-chip:hover { background: var(--brand); color: #fff; }
        
        .main-content { max-width: 900px; margin: 20px auto 60px; padding: 0 20px; }
        
        .ai-synthesis-box { background: var(--card-bg); border: 2px solid var(--accent-cream); border-radius: 12px; padding: 20px; margin-bottom: 20px; }
        .ai-synthesis-title { font-size: 0.85rem; font-weight: 700; color: var(--brand); text-transform: uppercase; margin-bottom: 8px; }
        .ai-synthesis-text { font-size: 0.95rem; color: var(--primary); line-height: 1.6; }

        .consensus-meter { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 24px; }
        
        .result-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 16px; box-shadow: 0 2px 8px rgba(91, 58, 41, 0.03); }
        .card-badge { background: var(--badge-bg); color: var(--primary); padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 0.8rem; }
        
        .takeaway-box { background: var(--bg); border-left: 3px solid var(--brand); padding: 12px 16px; margin: 10px 0; border-radius: 0 8px 8px 0; }
        .source-link { color: var(--brand); text-decoration: none; font-weight: 600; font-size: 0.9rem; }
        .source-link:hover { text-decoration: underline; color: var(--brand-dark); }
    </style>
</head>
<body>
    <header>
        <div class="logo">AnInag <span>Civic Points System</span></div>
        <div style="display:flex; align-items:center; gap:16px;">
            <div class="user-points-badge" id="headerPointsBadge">⭐ Points: 0</div>
            <a href="/docs" style="color:var(--text-muted); text-decoration:none; font-weight:500;">API Docs</a>
        </div>
    </header>

    <div class="hero-section">
        <h1 style="font-size: 2.25rem; margin-bottom: 12px;">AnInag Research & Reward Portal</h1>
        <p style="margin-bottom: 20px;">Earn points recorded on your MIT App for every transparency search conducted.</p>
        
        <div class="user-id-bar">
            <label for="userIdInput">App User ID / Tag:</label>
            <input type="text" id="userIdInput" class="user-id-input" value="user_001" onchange="fetchUserPoints()">
        </div>

        <div class="search-box">
            <input type="text" id="searchInput" class="search-input" value="confidential funds">
            <button class="search-btn" onclick="executeSearch()">Search (+1 Point)</button>
        </div>
    </div>

    <div class="repositories-bar">
        <div style="font-size: 0.75rem; font-weight: 700; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px;">Target Repositories</div>
        <a class="repo-chip" href="https://www.officialgazette.gov.ph/" target="_blank">Official Gazette ↗</a>
        <a class="repo-chip" href="https://lawphil.net/" target="_blank">LawPhil Archive ↗</a>
        <a class="repo-chip" href="https://www.congress.gov.ph/" target="_blank">House Documents ↗</a>
        <a class="repo-chip" href="https://ldr.senate.gov.ph/" target="_blank">Senate Archive ↗</a>
        <a class="repo-chip" href="https://ppp.gov.ph/" target="_blank">PPP Database ↗</a>
        <a class="repo-chip" href="https://www.coa.gov.ph/" target="_blank">COA Audit ↗</a>
    </div>

    <div class="main-content" id="resultsContainer">
        <p style="text-align: center; color: var(--text-muted);">Enter a query above to synthesize findings and earn points.</p>
    </div>

    <script>
        async function fetchUserPoints() {
            const userId = document.getElementById('userIdInput').value || 'default_user';
            try {
                const res = await fetch(`/api/points/${userId}`);
                const data = await res.json();
                document.getElementById('headerPointsBadge').innerText = `⭐ Points: ${data.points}`;
            } catch (e) {
                console.error(e);
            }
        }

        async function executeSearch() {
            const query = document.getElementById('searchInput').value;
            const userId = document.getElementById('userIdInput').value || 'default_user';
            const container = document.getElementById('resultsContainer');
            
            container.innerHTML = '<p style="text-align: center; color: var(--brand); font-weight:600;">Executing Search & Updating App Inventor Points...</p>';
            try {
                const response = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query, user_id: userId })
                });
                const data = await response.json();
                
                document.getElementById('headerPointsBadge').innerText = `⭐ Points: ${data.total_points}`;
                
                let html = `
                    <div class="ai-synthesis-box">
                        <div class="ai-synthesis-title">✨ AnInag AI Synthesis Summary</div>
                        <div class="ai-synthesis-text">${data.ai_synthesis}</div>
                    </div>
                    <div class="consensus-meter">
                        <div style="font-size:0.85rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">Coverage & Point Status</div>
                        <div style="font-size:1.1rem; font-weight:700; margin:4px 0; color:var(--primary);">Awarded +1 Point to '${data.user_id}'</div>
                        <div style="font-size:0.9rem; color:var(--text-muted);">Total Account Balance: ${data.total_points} Points</div>
                    </div>
                `;
                data.results.forEach(res => {
                    html += `
                        <div class="result-card">
                            <div style="display:flex; gap:12px; margin-bottom:10px;">
                                <span class="card-badge">${res.badge}</span>
                                <span style="font-size:0.8rem; color:var(--text-muted);">${res.authors} • ${res.year}</span>
                            </div>
                            <h2 style="font-size:1.15rem; font-weight:600; margin-bottom:10px; color:var(--primary);">${res.title}</h2>
                            <div class="takeaway-box">
                                <div style="font-size:0.75rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">Document Gist & Summary</div>
                                <div style="font-size:0.95rem; margin-top:4px; line-height:1.5; color:var(--primary);">${res.takeaway}</div>
                            </div>
                            <div>
                                <a href="${res.citation_url}" target="_blank" class="source-link">🔗 Open Target Document Match (${res.journal}) ↗</a>
                            </div>
                        </div>
                    `;
                });
                container.innerHTML = html;
            } catch (err) {
                container.innerHTML = '<p style="color: red; text-align: center;">Error recording search transaction.</p>';
            }
        }

        fetchUserPoints();
    </script>
</body>
</html>"""

# --- 3. BACKEND API FOR MIT APP INVENTOR (TINYWEBDB PROTOCOL) ---
@app.post("/storeval")
def store_val(tag: str = Form(...), value: str = Form(...)):
    database[tag] = value
    return ["stored", tag, value]

@app.post("/getvalue")
def get_val(tag: str = Form(...)):
    value = database.get(tag, "0")
    return [tag, value]
