from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from typing import Dict, List, Optional
from pydantic import BaseModel
from google import genai
import urllib.request
import urllib.parse
import json
import os
import re

app = FastAPI(title="AnInag AI Research & 4-Solution Chat")

DB_FILE = "database.json"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# --- PERSISTENT DISK STORAGE ---
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

# --- TARGET GOVERNMENT & NGO REPOSITORIES ---
GOVERNMENT_NGO_REPOSITORIES = [
    {
        "name": "Official Gazette of the Philippines",
        "url": "https://www.officialgazette.gov.ph/",
        "search_url_template": "https://www.officialgazette.gov.ph/?s={query}",
        "category": "Republic Acts & Executive Orders",
        "document_summary": "Publishes enacted Republic Acts, presidential issuances, administrative rules, and official state documents detailing legal mandates, budget allocations, and state expenditures."
    },
    {
        "name": "LawPhil Project (Arellano Law Foundation)",
        "url": "https://lawphil.net/statutes/repacts/repacts.html",
        "search_url_template": "https://www.google.com/search?q=site:lawphil.net+{query}",
        "category": "Statutory Archive & Legal Codes",
        "document_summary": "Maintains a comprehensive full-text statutory archive of Philippine legal codes, historic acts, and judicial provisions governing state transactions."
    },
    {
        "name": "House of Representatives Legislative Documents",
        "url": "https://www.congress.gov.ph/legislative-documents",
        "search_url_template": "https://www.congress.gov.ph/legislative-documents/?q={query}",
        "category": "House Bills & Congressional Records",
        "document_summary": "Archives filed House Bills, committee hearing transcripts, budgetary appropriations resolutions, and legislative tracking records."
    },
    {
        "name": "Senate Legislative Document Archive",
        "url": "https://ldr.senate.gov.ph/subject/government-documents",
        "search_url_template": "https://ldr.senate.gov.ph/subject/government-documents?search={query}",
        "category": "Senate Bills & Public Records",
        "document_summary": "Provides official records of Senate Bills, committee inquiries, policy position papers, and national policy deliberations."
    },
    {
        "name": "Public-Private Partnership (PPP) Center Project Database",
        "url": "https://ppp.gov.ph/project-database/",
        "search_url_template": "https://ppp.gov.ph/?s={query}",
        "category": "Infrastructure & Project Registry",
        "document_summary": "Catalogs infrastructure project pipelines, financial feasibility studies, contractual agreements, and bidding parameters for public-private ventures."
    },
    {
        "name": "Commission on Audit (COA) Documentary Guidelines",
        "url": "https://www.coa.gov.ph/FAQS/is-there-a-manual-or-list-of-documentary-requirements-for-common-government-transactions/",
        "search_url_template": "https://www.coa.gov.ph/?s={query}",
        "category": "Audit Compliance & Financial Records",
        "document_summary": "Details mandatory audit checklists, voucher requirements, disbursement rules, and compliance standards for public fund utilization."
    }
]


class SearchQuery(BaseModel):
    query: str
    user_id: Optional[str] = "user_001"
    sources: Optional[List[str]] = None


def fetch_page_excerpt(url: str, max_chars: int = 2500) -> str:
    """Fetches actual text from a linked document/page so Gemini can summarize it."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw_html = resp.read().decode("utf-8", errors="ignore")
            raw_html = re.sub(r"<(script|style).*?>.*?</\1>", " ", raw_html, flags=re.S | re.I)
            text = re.sub(r"<[^<]+?>", " ", raw_html)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:max_chars]
    except Exception:
        return ""


def fetch_web_documents(query: str) -> List[Dict]:
    """Fetches relevant public web documents and extracts text snippets for Gemini summarization."""
    encoded_query = urllib.parse.quote(f"{query} site:gov.ph OR site:org OR site:lawphil.net")
    url = f"https://html.ecosia.org/search?q={encoded_query}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    extracted_results = []
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode("utf-8", errors="ignore")
            matches = re.findall(r'<a class="result-title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
            for href, title in matches[:2]:
                clean_title = re.sub(r"<[^<]+?>", "", title).strip()
                excerpt = fetch_page_excerpt(href, max_chars=2200)
                fragment_url = f"{href}#:~:text={urllib.parse.quote(query)}"
                extracted_results.append({
                    "title": clean_title,
                    "url": fragment_url,
                    "raw_url": href,
                    "excerpt": excerpt
                })
    except Exception:
        pass

    return extracted_results


def generate_gemini_analysis(prompt: str, sources_context: str) -> dict:
    """Calls Gemini 2.5 Flash to summarize each source/document and generate 4 tailored solutions."""
    if not ai_client:
        return {
            "ai_synthesis": "GEMINI_API_KEY is missing on Render. Add GEMINI_API_KEY under Render -> Environment to activate live AI summaries and 4 solutions.",
            "source_summaries": {},
            "solutions": []
        }

    schema = {
        "type": "OBJECT",
        "properties": {
            "overall_summary": {"type": "STRING"},
            "source_summaries": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "source_name": {"type": "STRING"},
                        "summary": {"type": "STRING"}
                    },
                    "required": ["source_name", "summary"]
                }
            },
            "solutions": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "number": {"type": "STRING"},
                        "title": {"type": "STRING"},
                        "focus": {"type": "STRING"},
                        "description": {"type": "STRING"}
                    },
                    "required": ["number", "title", "focus", "description"]
                }
            }
        },
        "required": ["overall_summary", "source_summaries", "solutions"]
    }

    ai_prompt = f"""
You are AnInag, a civic research and document summarization assistant.
User Prompt: "{prompt}"

Retrieved Sources & Document Excerpts:
{sources_context}

Instructions:
1. "overall_summary": Summarize the retrieved document excerpts and repository context directly in relation to the User Prompt. Do not invent facts outside the provided text.
2. "source_summaries": For EVERY source block listed above (matching the exact "source_name" inside brackets), write a clear, prompt-aligned summary explaining how that document or repository informs the User Prompt.
3. "solutions": Generate 4 distinct, concrete, actionable solutions (numbered "Solution 1", "Solution 2", "Solution 3", "Solution 4") specifically tailored to solve or address the exact User Prompt.
"""

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=ai_prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": schema,
            },
        )
        data = json.loads(response.text)
        summary_map = {
            item["source_name"]: item["summary"]
            for item in data.get("source_summaries", [])
        }
        return {
            "ai_synthesis": data.get("overall_summary", ""),
            "source_summaries": summary_map,
            "solutions": data.get("solutions", [])[:4]
        }
    except Exception as e:
        return {
            "ai_synthesis": f"AI summarization error: {str(e)}",
            "source_summaries": {},
            "solutions": []
        }


# --- 1. CHAT PROMPT, GEMINI SUMMARIES & POINT REWARD ENDPOINT ---
@app.post("/api/search")
def search_ngo_data(payload: SearchQuery):
    raw_prompt = payload.query.strip()
    user_query = raw_prompt.lower()
    user_id = payload.user_id.strip() if payload.user_id else "user_001"
    encoded_user_query = urllib.parse.quote(user_query)

    # +1 point per prompt, 6 points = 1 tree
    point_tag = f"points_{user_id}"
    current_points = int(database.get(point_tag, "0"))
    new_points = current_points + 1
    database[point_tag] = str(new_points)
    save_database(database)
    trees_planted = new_points // 6

    web_docs = fetch_web_documents(raw_prompt)

    context_chunks = []
    results = []

    for idx, repo in enumerate(GOVERNMENT_NGO_REPOSITORIES):
        direct_deep_link = repo["search_url_template"].format(query=encoded_user_query)
        source_label = f"{repo['name']} ({repo['category']})"
        context_chunks.append(f"[{source_label}]\n{repo['document_summary']}")

        results.append({
            "id": idx + 1,
            "title": f"{repo['name']}: Records for '{raw_prompt}'",
            "source_key": source_label,
            "authors": repo["category"],
            "year": 2026,
            "journal": repo["name"],
            "takeaway": repo["document_summary"],
            "citation_url": direct_deep_link,
            "badge": "Official Target Repository"
        })

    for idx, doc in enumerate(web_docs):
        source_label = f"{doc['title']} (Web Document)"
        excerpt_text = doc["excerpt"] if doc["excerpt"] else "No full text excerpt retrieved; title and URL matched."
        context_chunks.append(f"[{source_label}]\nURL: {doc['raw_url']}\nExcerpt: {excerpt_text}")

        results.append({
            "id": len(GOVERNMENT_NGO_REPOSITORIES) + idx + 1,
            "title": doc["title"],
            "source_key": source_label,
            "authors": "Web Indexed Document",
            "year": 2026,
            "journal": "Public Web Document",
            "takeaway": excerpt_text[:300],
            "citation_url": doc["url"],
            "badge": "Live Document Source"
        })

    ai_output = generate_gemini_analysis(raw_prompt, "\n\n".join(context_chunks))
    ai_source_summaries = ai_output.get("source_summaries", {})

    for r in results:
        if r["source_key"] in ai_source_summaries and ai_source_summaries[r["source_key"]]:
            r["takeaway"] = ai_source_summaries[r["source_key"]]

    return {
        "query": raw_prompt,
        "user_id": user_id,
        "points_earned": 1,
        "total_points": new_points,
        "trees_planted": trees_planted,
        "ai_synthesis": ai_output.get("ai_synthesis", ""),
        "solutions": ai_output.get("solutions", []),
        "results": results
    }


@app.get("/api/points/{user_id}")
def get_user_points(user_id: str):
    points = int(database.get(f"points_{user_id}", "0"))
    trees = points // 6
    return {"user_id": user_id, "points": points, "trees": trees}


# --- 2. CONVERSATIONAL CHATBOT FRONTEND ---
@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AnInag AI Chat - Document Summaries & 4 Solutions</title>
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
        body { background-color: var(--bg); color: var(--text-main); min-height: 100vh; display: flex; flex-direction: column; }
        header {
            background: var(--card-bg); border-bottom: 2px solid var(--border);
            padding: 14px 28px; display: flex; justify-content: space-between; align-items: center;
            flex-wrap: wrap; gap: 12px; position: sticky; top: 0; z-index: 10;
        }
        .logo { font-size: 1.3rem; font-weight: 800; color: var(--primary); display: flex; align-items: center; gap: 8px; }
        .logo span { background: var(--brand); color: #fff; padding: 2px 8px; border-radius: 6px; font-size: 0.72rem; }
        .header-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .user-id-group { display: flex; align-items: center; gap: 6px; font-size: 0.82rem; color: var(--text-muted); }
        .user-id-input {
            border: 1px solid var(--border); background: var(--bg); padding: 5px 10px;
            border-radius: 8px; outline: none; font-weight: 600; color: var(--primary); width: 110px;
        }
        .user-points-badge {
            background: var(--accent-cream); border: 1px solid var(--border); color: var(--primary);
            padding: 6px 14px; border-radius: 20px; font-weight: 700; font-size: 0.85rem;
        }
        .chat-shell {
            max-width: 940px; width: 100%; margin: 0 auto; padding: 20px 18px 130px;
            flex: 1; display: flex; flex-direction: column; gap: 18px;
        }
        .welcome-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 16px; padding: 22px; }
        .welcome-card h1 { font-size: 1.45rem; color: var(--primary); margin-bottom: 6px; }
        .welcome-card p { font-size: 0.92rem; color: var(--text-muted); margin-bottom: 14px; }
        .repo-chips { display: flex; flex-wrap: wrap; gap: 6px; }
        .repo-chip {
            background: var(--accent-cream); border: 1px solid var(--border); padding: 4px 10px;
            border-radius: 20px; font-size: 0.74rem; color: var(--primary); text-decoration: none; font-weight: 600;
        }
        .repo-chip:hover { background: var(--brand); color: #fff; }
        .message-row { display: flex; flex-direction: column; gap: 6px; }
        .user-bubble {
            align-self: flex-end; background: var(--brand); color: #fff; padding: 12px 18px;
            border-radius: 18px 18px 4px 18px; max-width: 78%; font-size: 0.95rem; line-height: 1.45;
        }
        .user-meta { align-self: flex-end; font-size: 0.75rem; color: var(--text-muted); }
        .bot-card {
            background: var(--card-bg); border: 1px solid var(--border); border-radius: 16px;
            padding: 22px; box-shadow: 0 2px 10px rgba(91, 58, 41, 0.04); display: flex; flex-direction: column; gap: 18px;
        }
        .bot-header {
            display: flex; justify-content: space-between; align-items: center;
            flex-wrap: wrap; gap: 8px; border-bottom: 1px solid var(--border); padding-bottom: 10px;
        }
        .bot-title { font-weight: 700; font-size: 0.9rem; color: var(--brand); text-transform: uppercase; }
        .reward-pill {
            background: var(--bg); border: 1px solid var(--border); padding: 4px 10px;
            border-radius: 12px; font-size: 0.78rem; font-weight: 600; color: var(--primary);
        }
        .section-heading { font-size: 0.85rem; font-weight: 700; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px; }
        .synthesis-box {
            border: 2px solid var(--accent-cream); border-radius: 12px; padding: 14px 16px;
            background: #FCFBF5; font-size: 0.94rem; line-height: 1.55;
        }
        .solutions-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
        .solution-card {
            background: var(--bg); border: 1px solid var(--border); border-radius: 12px;
            padding: 14px; display: flex; flex-direction: column; gap: 6px;
        }
        .solution-badge {
            align-self: flex-start; background: var(--brand); color: #fff;
            font-size: 0.72rem; font-weight: 700; padding: 2px 8px; border-radius: 6px;
        }
        .solution-title { font-size: 0.98rem; font-weight: 700; color: var(--primary); }
        .solution-focus { font-size: 0.78rem; font-weight: 600; color: var(--text-muted); }
        .solution-desc { font-size: 0.87rem; line-height: 1.5; color: var(--primary); }
        .source-card {
            background: var(--card-bg); border: 1px solid var(--border);
            border-radius: 12px; padding: 16px; margin-bottom: 12px;
        }
        .source-badge {
            background: var(--badge-bg); color: var(--primary); padding: 2px 8px;
            border-radius: 4px; font-weight: 600; font-size: 0.78rem;
        }
        .takeaway-box {
            background: var(--bg); border-left: 3px solid var(--brand);
            padding: 12px 14px; margin: 10px 0; border-radius: 0 8px 8px 0;
        }
        .source-link { color: var(--brand); text-decoration: none; font-weight: 600; font-size: 0.88rem; }
        .source-link:hover { text-decoration: underline; color: var(--brand-dark); }
        .composer-bar {
            position: fixed; bottom: 0; left: 0; right: 0;
            background: var(--card-bg); border-top: 2px solid var(--border); padding: 12px 18px; z-index: 20;
        }
        .composer-inner { max-width: 940px; margin: 0 auto; display: flex; gap: 10px; align-items: center; }
        .prompt-input {
            flex: 1; border: 2px solid var(--border); border-radius: 14px; padding: 12px 16px;
            font-size: 0.95rem; color: var(--primary); background: var(--bg); outline: none;
        }
        .prompt-input:focus { border-color: var(--brand); background: #fff; }
        .send-btn {
            background: var(--brand); color: #fff; border: none; border-radius: 12px;
            padding: 12px 20px; font-weight: 700; font-size: 0.9rem; cursor: pointer; white-space: nowrap;
        }
        .send-btn:hover { background: var(--brand-dark); }
    </style>
</head>
<body>
    <header>
        <div class="logo">AnInag AI Chat <span>Gemini Summaries + 4 Solutions</span></div>
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
            <h1>AnInag AI Document Summarizer & Solution Generator</h1>
            <p>Enter any prompt to summarize matching public documents and repositories, generate <strong>4 tailored solutions</strong>, and earn <strong>+1 point</strong> per prompt (<strong>6 points = 1 tree</strong>) synced with your MIT App Inventor app.</p>
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
                placeholder="Enter your prompt (e.g., public market cooling and waste management)..."
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
            loadingCard.innerHTML = `<div style="color:var(--brand); font-weight:600;">Summarizing documents, generating 4 tailored solutions, and syncing points...</div>`;
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

                const solutionsHtml = (data.solutions || []).map(sol => `
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
                            <div style="font-size:0.74rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">AI Document Summary</div>
                            <div style="font-size:0.92rem; margin-top:4px; line-height:1.5; color:var(--primary);">${escapeHtml(res.takeaway)}</div>
                        </div>
                        <a href="${escapeHtml(res.citation_url)}" target="_blank" class="source-link">🔗 Open Source (${escapeHtml(res.journal)}) ↗</a>
                    </div>
                `).join('');

                loadingCard.innerHTML = `
                    <div class="bot-header">
                        <div class="bot-title">AI Document Synthesis, 4 Solutions & Source Summaries</div>
                        <div class="reward-pill">+1 Pt → Total: ${data.total_points} | 🌳 Trees: ${data.trees_planted}</div>
                    </div>
                    <div>
                        <div class="section-heading">Overall AI Document Summary</div>
                        <div class="synthesis-box">${escapeHtml(data.ai_synthesis)}</div>
                    </div>
                    <div>
                        <div class="section-heading">4 Tailored Solutions</div>
                        <div class="solutions-grid">${solutionsHtml || '<div style="font-size:0.9rem; color:var(--text-muted);">No solutions returned. Check GEMINI_API_KEY in Render.</div>'}</div>
                    </div>
                    <div>
                        <div class="section-heading">AI Summaries per Source</div>
                        ${sourcesHtml}
                    </div>
                `;
                loadingCard.scrollIntoView({ behavior: 'smooth', block: 'end' });
            } catch (err) {
                loadingCard.innerHTML = `<div style="color:#8F4E3E; font-weight:600;">Error communicating with backend server.</div>`;
            }
        }

        fetchUserPoints();
    </script>
</body>
</html>"""


# --- 3. TINYWEBDB ENDPOINTS FOR MIT APP INVENTOR ---
@app.post("/storeval")
def store_val(tag: str = Form(...), value: str = Form(...)):
    database[tag] = value
    save_database(database)
    return ["stored", tag, value]


@app.post("/getvalue")
def get_val(tag: str = Form(...)):
    value = database.get(tag, "0")
    return [tag, value]
