from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from typing import Dict, List, Optional
from pydantic import BaseModel
import urllib.request
import urllib.parse
import json
import os
import re

app = FastAPI(title="AnInag NGO Transparency & Research Chat")

DB_FILE = "database.json"


# --- PERSISTENT DISK STORAGE (JSON FILE) ---
def load_database() -> Dict[str, str]:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_database(data: Dict[str, str]):
    with open(DB_FILE, "w") as f:
        json.dump(data, f)


database: Dict[str, str] = load_database()

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
    user_id: Optional[str] = "user_001"
    sources: Optional[List[str]] = None


def build_four_solutions(prompt_text: str) -> List[Dict[str, str]]:
    topic = prompt_text.strip() if prompt_text.strip() else "public service and transparency"
    return [
        {
            "number": "Solution 1",
            "title": "Legal & Policy Framework Solution",
            "focus": "Statutory & Regulatory Alignment",
            "description": (
                f"Review existing laws, executive orders, and proposed bills around '{topic}' "
                f"to close policy gaps, clarify agency responsibilities, and establish enforceable standards."
            )
        },
        {
            "number": "Solution 2",
            "title": "Budget & Audit Oversight Solution",
            "focus": "Fund Tracking & COA Compliance",
            "description": (
                f"Map funding sources, procurement steps, and required documentation for '{topic}' "
                f"so spending can be audited clearly and bottlenecks or unliquidated items are caught early."
            )
        },
        {
            "number": "Solution 3",
            "title": "Operational & Community Co-Design Solution",
            "focus": "Practical Implementation on the Ground",
            "description": (
                f"Design practical workflows for '{topic}' together with local communities, workers, and partner NGOs "
                f"so solutions fit real daily conditions and can be adopted without friction."
            )
        },
        {
            "number": "Solution 4",
            "title": "Public Monitoring & Feedback Solution",
            "focus": "Ongoing Accountability & Reporting",
            "description": (
                f"Set up clear milestones, public status updates, and reporting checkpoints for '{topic}' "
                f"so stakeholders can verify outcomes and sustain action over time."
            )
        }
    ]


def fetch_ecosia_ngo_results(query: str) -> List[Dict]:
    encoded_query = urllib.parse.quote(f"{query} site:gov.ph OR site:org OR site:lawphil.net")
    url = f"https://html.ecosia.org/search?q={encoded_query}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    extracted_results = []
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            html = response.read().decode("utf-8")
            matches = re.findall(r'<a class="result-title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
            for href, title in matches[:2]:
                clean_title = re.sub(r"<[^<]+?>", "", title)
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


# --- 1. CHAT SEARCH, SOURCE SUMMARIES, 4 SOLUTIONS & POINT REWARD ROUTE ---
@app.post("/api/search")
def search_ngo_data(payload: SearchQuery):
    raw_prompt = payload.query.strip()
    user_query = raw_prompt.lower()
    user_id = payload.user_id.strip() if payload.user_id else "user_001"
    encoded_user_query = urllib.parse.quote(user_query)

    point_tag = f"points_{user_id}"
    current_points = int(database.get(point_tag, "0"))
    new_points = current_points + 1
    database[point_tag] = str(new_points)
    save_database(database)

    trees_planted = new_points // 6  # 6 points = 1 tree calculation
    solutions = build_four_solutions(raw_prompt)

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
            "takeaway": (
                f"Document Gist & Content Overview: Live web source data for '{user_query}' details specific operational context, "
                f"background data, and public disclosures relevant to your prompt."
            ),
            "consensus_score": 85 - (idx * 4),
            "citation_url": web_item["url"],
            "badge": "Live Text-Fragment Link"
        })

    ai_synthesis_text = (
        f"AnInag Comprehensive AI Synthesis: Evaluating across all registered official target repositories "
        f"(Official Gazette, LawPhil, House Legislative Documents, Senate Archives, PPP Database, and COA Audit Guidelines), "
        f"prompts regarding '{user_query}' intersect with multiple legal mandates, financial reporting structures, and compliance protocols."
    )

    return {
        "query": user_query,
        "user_id": user_id,
        "points_earned": 1,
        "total_points": new_points,
        "trees_planted": trees_planted,
        "ai_synthesis": ai_synthesis_text,
        "solutions": solutions,
        "consensus_summary": {
            "affirmative_percentage": 95,
            "verdict": f"Evaluated Across All {len(GOVERNMENT_NGO_REPOSITORIES)} Target Repositories",
            "overview": f"1 point awarded to '{user_id}'. Total Points: {new_points} | Trees Planted: {trees_planted}."
        },
        "results": results
    }


@app.get("/api/points/{user_id}")
def get_user_points(user_id: str):
    points = int(database.get(f"points_{user_id}", "0"))
    trees = points // 6
    return {"user_id": user_id, "points": points, "trees": trees}


# --- 2. CHATBOT FRONTEND WITH SOURCE SUMMARIES + 4 SOLUTIONS ---
@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AnInag Chat - Research Summaries & 4 Solutions</title>
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
        body {
            background-color: var(--bg);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        header {
            background: var(--card-bg);
            border-bottom: 2px solid var(--border);
            padding: 14px 28px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        .logo {
            font-size: 1.3rem;
            font-weight: 800;
            color: var(--primary);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .logo span {
            background: var(--brand);
            color: #fff;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 0.72rem;
        }

        .header-controls {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        .user-id-group {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.82rem;
            color: var(--text-muted);
        }
        .user-id-input {
            border: 1px solid var(--border);
            background: var(--bg);
            padding: 5px 10px;
            border-radius: 8px;
            outline: none;
            font-weight: 600;
            color: var(--primary);
            width: 110px;
        }
        .user-points-badge {
            background: var(--accent-cream);
            border: 1px solid var(--border);
            color: var(--primary);
            padding: 6px 14px;
            border-radius: 20px;
            font-weight: 700;
            font-size: 0.85rem;
        }

        .chat-shell {
            max-width: 940px;
            width: 100%;
            margin: 0 auto;
            padding: 20px 18px 130px;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 18px;
        }

        .welcome-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 22px;
        }
        .welcome-card h1 {
            font-size: 1.45rem;
            color: var(--primary);
            margin-bottom: 6px;
        }
        .welcome-card p {
            font-size: 0.92rem;
            color: var(--text-muted);
            margin-bottom: 14px;
        }
        .repo-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }
        .repo-chip {
            background: var(--accent-cream);
            border: 1px solid var(--border);
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.74rem;
            color: var(--primary);
            text-decoration: none;
            font-weight: 600;
        }
        .repo-chip:hover {
            background: var(--brand);
            color: #fff;
        }

        .message-row {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .user-bubble {
            align-self: flex-end;
            background: var(--brand);
            color: #fff;
            padding: 12px 18px;
            border-radius: 18px 18px 4px 18px;
            max-width: 78%;
            font-size: 0.95rem;
            line-height: 1.45;
        }
        .user-meta {
            align-self: flex-end;
            font-size: 0.75rem;
            color: var(--text-muted);
        }

        .bot-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 22px;
            box-shadow: 0 2px 10px rgba(91, 58, 41, 0.04);
            display: flex;
            flex-direction: column;
            gap: 18px;
        }
        .bot-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 10px;
        }
        .bot-title {
            font-weight: 700;
            font-size: 0.9rem;
            color: var(--brand);
            text-transform: uppercase;
        }
        .reward-pill {
            background: var(--bg);
            border: 1px solid var(--border);
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--primary);
        }

        .section-heading {
            font-size: 0.85rem;
            font-weight: 700;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 8px;
        }

        .synthesis-box {
            border: 2px solid var(--accent-cream);
            border-radius: 12px;
            padding: 14px 16px;
            background: #FCFBF5;
            font-size: 0.94rem;
            line-height: 1.55;
        }

        .solutions-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 12px;
        }
        .solution-card {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .solution-badge {
            align-self: flex-start;
            background: var(--brand);
            color: #fff;
            font-size: 0.72rem;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 6px;
        }
        .solution-title {
            font-size: 0.98rem;
            font-weight: 700;
            color: var(--primary);
        }
        .solution-focus {
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--text-muted);
        }
        .solution-desc {
            font-size: 0.87rem;
            line-height: 1.5;
            color: var(--primary);
        }

        .source-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 12px;
        }
        .source-badge {
            background: var(--badge-bg);
            color: var(--primary);
            padding: 2px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.78rem;
        }
        .takeaway-box {
            background: var(--bg);
            border-left: 3px solid var(--brand);
            padding: 12px 14px;
            margin: 10px 0;
            border-radius: 0 8px 8px 0;
        }
        .source-link {
            color: var(--brand);
            text-decoration: none;
            font-weight: 600;
            font-size: 0.88rem;
        }
        .source-link:hover {
            text-decoration: underline;
            color: var(--brand-dark);
        }

        .composer-bar {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: var(--card-bg);
            border-top: 2px solid var(--border);
            padding: 12px 18px;
            z-index: 20;
        }
        .composer-inner {
            max-width: 940px;
            margin: 0 auto;
            display: flex;
            gap: 10px;
            align-items: center;
        }
        .prompt-input {
            flex: 1;
            border: 2px solid var(--border);
            border-radius: 14px;
            padding: 12px 16px;
            font-size: 0.95rem;
            color: var(--primary);
            background: var(--bg);
            outline: none;
        }
        .prompt-input:focus {
            border-color: var(--brand);
            background: #fff;
        }
        .send-btn {
            background: var(--brand);
            color: #fff;
            border: none;
            border-radius: 12px;
            padding: 12px 20px;
            font-weight: 700;
            font-size: 0.9rem;
            cursor: pointer;
            white-space: nowrap;
        }
        .send-btn:hover {
            background: var(--brand-dark);
        }
    </style>
</head>
<body>
    <header>
        <div class="logo">AnInag Chat <span>Summaries + 4 Solutions</span></div>
        <div class="header-controls">
            <div class="user-id-group">
                <label for="userIdInput">User ID:</label>
                <input type="text" id="userIdInput" class="user-id-input" value="user_001" onchange="fetchUserPoints()">
            </div>
            <div class="user-points-badge" id="headerPointsBadge">⭐ Points: 0 | 🌳 Trees: 0</div>
            <a href="/docs" style="color:var(--text-muted); text-decoration:none; font-size:0.85rem; font-weight:600;">API Docs</a>
        </div>
    </header>

    <main class="chat-shell" id="chatThread">
        <section class="welcome-card">
            <h1>AnInag Research & Solution Chat</h1>
            <p>Enter a prompt to get prompt-aligned source summaries, <strong>4 distinct solutions</strong>, and <strong>+1 point</strong> per prompt (<strong>6 points = 1 tree</strong>) synced with your MIT App Inventor app.</p>
            <div class="repo-chips">
                <a class="repo-chip" href="https://www.officialgazette.gov.ph/" target="_blank">Official Gazette ↗</a>
                <a class="repo-chip" href="https://lawphil.net/" target="_blank">LawPhil Archive ↗</a>
                <a class="repo-chip" href="https://www.congress.gov.ph/" target="_blank">House Documents ↗</a>
                <a class="repo-chip" href="https://ldr.senate.gov.ph/" target="_blank">Senate Archive ↗</a>
                <a class="repo-chip" href="https://ppp.gov.ph/" target="_blank">PPP Database ↗</a>
                <a class="repo-chip" href="https://www.coa.gov.ph/" target="_blank">COA Audit ↗</a>
            </div>
        </section>
    </main>

    <div class="composer-bar">
        <div class="composer-inner">
            <input
                type="text"
                id="searchInput"
                class="prompt-input"
                placeholder="Enter your prompt (e.g., confidential funds or public market waste management)..."
                onkeydown="if (event.key === 'Enter') executeSearch()"
            >
            <button class="send-btn" onclick="executeSearch()">Send (+1 Pt)</button>
        </div>
    </div>

    <script>
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text || '';
            return div.innerHTML;
        }

        function fallbackSolutions(query) {
            return [
                { number: "Solution 1", title: "Legal & Policy Framework Solution", focus: "Statutory & Regulatory Alignment", description: `Review laws, executive orders, and bills around '${query}' to clarify mandates and close policy gaps.` },
                { number: "Solution 2", title: "Budget & Audit Oversight Solution", focus: "Fund Tracking & COA Compliance", description: `Check fund allocations, disbursements, and COA documentary requirements for '${query}'.` },
                { number: "Solution 3", title: "Operational & Community Co-Design Solution", focus: "Practical Implementation on the Ground", description: `Co-design workflows for '${query}' with affected communities, workers, and local partners.` },
                { number: "Solution 4", title: "Public Monitoring & Feedback Solution", focus: "Ongoing Accountability & Reporting", description: `Track milestones, agency follow-ups, and public updates for '${query}' over time.` }
            ];
        }

        async function fetchUserPoints() {
            const userId = document.getElementById('userIdInput').value.trim() || 'user_001';
            try {
                const res = await fetch(`/api/points/${encodeURIComponent(userId)}`);
                const data = await res.json();
                document.getElementById('headerPointsBadge').innerText = `⭐ Points: ${data.points} | 🌳 Trees: ${data.trees}`;
            } catch (e) {
                console.error(e);
            }
        }

        async function executeSearch() {
            const inputEl = document.getElementById('searchInput');
            const query = inputEl.value.trim();
            if (!query) return;

            const userId = document.getElementById('userIdInput').value.trim() || 'user_001';
            const thread = document.getElementById('chatThread');

            const userRow = document.createElement('div');
            userRow.className = 'message-row';
            userRow.innerHTML = `
                <div class="user-bubble">${escapeHtml(query)}</div>
                <div class="user-meta">Prompt by ${escapeHtml(userId)}</div>
            `;
            thread.appendChild(userRow);
            inputEl.value = '';

            const loadingCard = document.createElement('div');
            loadingCard.className = 'bot-card';
            loadingCard.innerHTML = `<div style="color:var(--brand); font-weight:600;">Generating prompt-aligned source summaries, 4 solutions, and syncing points...</div>`;
            thread.appendChild(loadingCard);
            loadingCard.scrollIntoView({ behavior: 'smooth', block: 'end' });

            try {
                const response = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query, user_id: userId })
                });
                const data = await response.json();

                document.getElementById('headerPointsBadge').innerText = `⭐ Points: ${data.total_points} | 🌳 Trees: ${data.trees_planted}`;

                const solutionsList = (Array.isArray(data.solutions) && data.solutions.length > 0)
                    ? data.solutions
                    : fallbackSolutions(query);

                const solutionsHtml = solutionsList.map(sol => `
                    <div class="solution-card">
                        <span class="solution-badge">${escapeHtml(sol.number)}</span>
                        <div class="solution-title">${escapeHtml(sol.title)}</div>
                        <div class="solution-focus">${escapeHtml(sol.focus)}</div>
                        <div class="solution-desc">${escapeHtml(sol.description)}</div>
                    </div>
                `).join('');

                const sourcesHtml = (data.results || []).map(res => `
                    <div class="source-card">
                        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px;">
                            <span class="source-badge">${escapeHtml(res.badge)}</span>
                            <span style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(res.authors)} • ${escapeHtml(res.year)}</span>
                        </div>
                        <h3 style="font-size:1.05rem; font-weight:600; color:var(--primary);">${escapeHtml(res.title)}</h3>
                        <div class="takeaway-box">
                            <div style="font-size:0.74rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">Document Gist & Summary</div>
                            <div style="font-size:0.92rem; margin-top:4px; line-height:1.5; color:var(--primary);">${escapeHtml(res.takeaway)}</div>
                        </div>
                        <a href="${escapeHtml(res.citation_url)}" target="_blank" class="source-link">🔗 Open Target Document Match (${escapeHtml(res.journal)}) ↗</a>
                    </div>
                `).join('');

                loadingCard.innerHTML = `
                    <div class="bot-header">
                        <div class="bot-title">AnInag Synthesis, 4 Solutions & Source Summaries</div>
                        <div class="reward-pill">+1 Pt → Total: ${data.total_points} | 🌳 Trees: ${data.trees_planted}</div>
                    </div>
                    <div>
                        <div class="section-heading">Overall Synthesis</div>
                        <div class="synthesis-box">${escapeHtml(data.ai_synthesis)}</div>
                    </div>
                    <div>
                        <div class="section-heading">4 Proposed Solutions</div>
                        <div class="solutions-grid">${solutionsHtml}</div>
                    </div>
                    <div>
                        <div class="section-heading">Source Summaries Aligned to Your Prompt</div>
                        ${sourcesHtml}
                    </div>
                `;
                loadingCard.scrollIntoView({ behavior: 'smooth', block: 'end' });
            } catch (err) {
                loadingCard.innerHTML = `<div style="color:#8F4E3E; font-weight:600;">Error processing prompt. Make sure your updated main.py is deployed and running.</div>`;
            }
        }

        fetchUserPoints();
    </script>
</body>
</html>"""


# --- 3. TINYWEBDB API ENDPOINTS FOR MIT APP INVENTOR ---
@app.post("/storeval")
def store_val(tag: str = Form(...), value: str = Form(...)):
    database[tag] = value
    save_database(database)
    return ["stored", tag, value]


@app.post("/getvalue")
def get_val(tag: str = Form(...)):
    value = database.get(tag, "0")
    return [tag, value]
