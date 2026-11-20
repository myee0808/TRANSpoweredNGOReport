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


class SearchQuery(BaseModel):
    query: str
    user_id: Optional[str] = "user_001"
    sources: Optional[List[str]] = None


def is_relevant_to_query(query: str, title: str, text: str) -> bool:
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", query) if len(w) > 2]
    if not words:
        return True
    haystack = f"{title} {text}".lower()
    return any(w in haystack for w in words)


def fetch_page_excerpt(url: str, max_chars: int = 1200) -> str:
    """Fetches a short text excerpt from a page so AI can summarize it."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            raw_html = resp.read().decode("utf-8", errors="ignore")
            raw_html = re.sub(r"<(script|style).*?>.*?</\1>", " ", raw_html, flags=re.S | re.I)
            text = re.sub(r"<[^<]+?>", " ", raw_html)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:max_chars]
    except Exception:
        return ""


def fetch_web_documents(query: str) -> List[Dict]:
    """Fetches web results and keeps only sources that match the user's prompt."""
    encoded_query = urllib.parse.quote(f"{query} (site:gov.ph OR site:org OR site:lawphil.net)")
    url = f"https://html.ecosia.org/search?q={encoded_query}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    extracted_results = []
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            html = response.read().decode("utf-8", errors="ignore")
            matches = re.findall(r'<a class="result-title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
            for href, title in matches[:5]:
                clean_title = re.sub(r"<[^<]+?>", "", title).strip()
                if not clean_title or not href.startswith("http"):
                    continue
                excerpt = fetch_page_excerpt(href, max_chars=1200)
                if not is_relevant_to_query(query, clean_title, excerpt):
                    continue
                fragment_url = f"{href}#:~:text={urllib.parse.quote(query)}"
                extracted_results.append({
                    "title": clean_title,
                    "url": fragment_url,
                    "raw_url": href,
                    "excerpt": excerpt
                })
                if len(extracted_results) >= 3:
                    break
    except Exception:
        pass

    return extracted_results


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise


def generate_gemini_analysis(prompt: str, sources_context: str) -> dict:
    """Uses Gemini 3.8 Flash Interactions API to summarize sources and generate 4 tailored solutions."""
    if not ai_client:
        return {
            "ai_synthesis": "GEMINI_API_KEY is missing on Render. Add GEMINI_API_KEY under Render -> Environment.",
            "source_summaries": {},
            "solutions": []
        }

    context_section = (
        sources_context
        if sources_context.strip()
        else "No direct matching external document excerpts were retrieved for this prompt."
    )

    ai_prompt = f"""
You are AnInag, a civic research and problem-solving assistant. Write all output in clear, natural English.
User Prompt: "{prompt}"

Matched Sources & Excerpts:
{context_section}

Return ONLY valid JSON (no ```json code fences) with this exact structure:
{{
  "overall_summary": "Clear, concise summary aligned to the User Prompt based on the provided information.",
  "source_summaries": [
    {{
      "source_name": "Exact source_name inside brackets above (if any)",
      "summary": "Concise prompt-aligned summary of this source."
    }}
  ],
  "solutions": [
    {{
      "number": "Solution 1",
      "title": "Short solution title",
      "focus": "Focus area",
      "description": "Concrete, practical solution tailored to the User Prompt."
    }},
    {{
      "number": "Solution 2",
      "title": "Short solution title",
      "focus": "Focus area",
      "description": "Concrete, practical solution tailored to the User Prompt."
    }},
    {{
      "number": "Solution 3",
      "title": "Short solution title",
      "focus": "Focus area",
      "description": "Concrete, practical solution tailored to the User Prompt."
    }},
    {{
      "number": "Solution 4",
      "title": "Short solution title",
      "focus": "Focus area",
      "description": "Concrete, practical solution tailored to the User Prompt."
    }}
  ]
}}
"""

    try:
        interaction = ai_client.interactions.create(
            model="gemini-3.8-flash",
            input=ai_prompt,
            generation_config={
                "thinking_level": "low"
            },
        )
        raw_output = getattr(interaction, "output_text", "") or ""
        data = extract_json_object(raw_output)

        summary_map = {
            item.get("source_name", ""): item.get("summary", "")
            for item in data.get("source_summaries", [])
            if item.get("source_name")
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


# --- 1. CHAT PROMPT, AI SUMMARIES, 4 SOLUTIONS & POINT REWARD ---
@app.post("/api/search")
def search_ngo_data(payload: SearchQuery):
    raw_prompt = payload.query.strip()
    user_id = payload.user_id.strip() if payload.user_id else "user_001"

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

    for idx, doc in enumerate(web_docs):
        source_label = f"{doc['title']} (Matched Source)"
        excerpt_text = doc["excerpt"] if doc["excerpt"] else f"Matched result for '{raw_prompt}'."
        context_chunks.append(f"[{source_label}]\nURL: {doc['raw_url']}\nExcerpt: {excerpt_text}")

        results.append({
            "id": idx + 1,
            "title": doc["title"],
            "source_key": source_label,
            "authors": "Matched Source",
            "year": 2026,
            "journal": "Verified Result",
            "takeaway": excerpt_text[:280],
            "citation_url": doc["url"],
            "badge": "Relevant Match"
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
    <title>AnInag AI Chat - 4 Solutions & Matched Sources</title>
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
        .welcome-card p { font-size: 0.92rem; color: var(--text-muted); }
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
        .empty-sources-box {
            padding: 14px 16px; background: var(--bg); border: 1px dashed var(--border);
            border-radius: 12px; font-size: 0.88rem; color: var(--text-muted);
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
        <div class="logo">AnInag AI Chat <span>4 Solutions</span></div>
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
            <h1>AnInag AI Chatbot</h1>
            <p>Enter a prompt to get a summary, <strong>4 tailored solutions</strong>, and only relevant sources (no empty "no results" links). Every prompt awards <strong>+1 point</strong> (<strong>6 points = 1 tree</strong>) synced with MIT App Inventor.</p>
        </section>
    </main>

    <div class="composer-bar">
        <div class="composer-inner">
            <input
                type="text"
                id="searchInput"
                class="prompt-input"
                placeholder="Type your prompt here..."
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
            loadingCard.innerHTML = `<div style="color:var(--brand); font-weight:600;">Generating summary, 4 solutions, and checking relevant sources...</div>`;
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

                const sourcesHtml = (data.results && data.results.length > 0)
                    ? data.results.map(res => `
                        <div class="source-card">
                            <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px;">
                                <span class="source-badge">${escapeHtml(res.badge)}</span>
                                <span style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(res.authors)} • ${escapeHtml(res.year)}</span>
                            </div>
                            <h3 style="font-size:1.05rem; font-weight:600; color:var(--primary);">${escapeHtml(res.title)}</h3>
                            <div class="takeaway-box">
                                <div style="font-size:0.74rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">AI Summary</div>
                                <div style="font-size:0.92rem; margin-top:4px; line-height:1.5; color:var(--primary);">${escapeHtml(res.takeaway)}</div>
                            </div>
                            <a href="${escapeHtml(res.citation_url)}" target="_blank" class="source-link">🔗 Open Matched Source (${escapeHtml(res.journal)}) ↗</a>
                        </div>
                    `).join('')
                    : `<div class="empty-sources-box">No direct matching source links were found for this prompt, so no empty source links were displayed.</div>`;

                loadingCard.innerHTML = `
                    <div class="bot-header">
                        <div class="bot-title">AI Summary, 4 Solutions & Matched Sources</div>
                        <div class="reward-pill">+1 Pt → Total: ${data.total_points} | 🌳 Trees: ${data.trees_planted}</div>
                    </div>
                    <div>
                        <div class="section-heading">Summary</div>
                        <div class="synthesis-box">${escapeHtml(data.ai_synthesis)}</div>
                    </div>
                    <div>
                        <div class="section-heading">4 Tailored Solutions</div>
                        <div class="solutions-grid">${solutionsHtml || '<div style="font-size:0.9rem; color:var(--text-muted);">No solutions returned. Check GEMINI_API_KEY in Render.</div>'}</div>
                    </div>
                    <div>
                        <div class="section-heading">Relevant Sources</div>
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
