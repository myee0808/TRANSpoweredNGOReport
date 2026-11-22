from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.gzip import GZipMiddleware
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from pydantic import BaseModel
from google import genai
import urllib.request
import urllib.parse
import threading
import html as html_lib
import json
import time
import os
import re

app = FastAPI(title="AnInag AI Research & 4-Solution Chat")
# SPEED: compress the big HTML page and JSON replies (smaller = faster to download)
app.add_middleware(GZipMiddleware, minimum_size=1000)

DB_FILE = "database.json"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# STRICT ALLOWLIST: AnInag will ONLY read and cite these exact URLs
ALLOWED_URLS: List[str] = [
    "https://www.officialgazette.gov.ph/section/republic-acts/",
    "https://lawphil.net/statutes/repacts/repacts.html",
    "https://www.congress.gov.ph/legislative-documents",
    "https://ldr.senate.gov.ph/subject/government-documents",
    "https://ppp.gov.ph/project-database/",
    "https://www.coa.gov.ph/FAQS/is-there-a-manual-or-list-of-documentary-requirements-for-common-government-transactions/",
]

# SPEED: compile regexes once instead of on every request
RE_WORDS = re.compile(r"[a-zA-Z0-9]+")
RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
RE_SCRIPT_STYLE = re.compile(r"<(script|style).*?>.*?</\1>", re.S | re.I)
RE_TAGS = re.compile(r"<[^<]+?>")
RE_SPACES = re.compile(r"\s+")
RE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)
RE_JSON_OBJ = re.compile(r"\{.*\}", re.S)

# SPEED: one shared pool for fetching pages in parallel
FETCH_POOL = ThreadPoolExecutor(max_workers=6)

# SPEED: cap timeouts and bytes read so slow government sites don't stall requests
FAST_PAGE_TIMEOUT = 1.6
BATCH_WAIT_TIMEOUT = 1.8
FAST_MAX_PAGE_BYTES = 80_000


# --- PERSISTENT DISK STORAGE ---
db_lock = threading.Lock()


def load_database() -> Dict[str, str]:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_database(data: Dict[str, str]):
    # SAFE + FAST: write to a temp file then swap, so the file never gets corrupted
    tmp_file = DB_FILE + ".tmp"
    with db_lock:
        snapshot = dict(data)
    with open(tmp_file, "w") as f:
        json.dump(snapshot, f, separators=(",", ":"))
    os.replace(tmp_file, DB_FILE)


database: Dict[str, str] = load_database()


class SearchQuery(BaseModel):
    query: str
    user_id: Optional[str] = "user_001"
    sources: Optional[List[str]] = None


# --- SPEED: remember recent answers so repeated prompts are instant ---
CACHE_TTL_SECONDS = 60 * 30  # 30 minutes
CACHE_MAX_ITEMS = 200
_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


def cache_get(key: str):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.time() - item[0] < CACHE_TTL_SECONDS:
            return item[1]
        _cache.pop(key, None)
        return None


def cache_set(key: str, value):
    with _cache_lock:
        if len(_cache) >= CACHE_MAX_ITEMS:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[key] = (time.time(), value)


def clean_html_text(text: str) -> str:
    return RE_SPACES.sub(" ", html_lib.unescape(RE_TAGS.sub(" ", text))).strip()


def get_domain(url: str) -> str:
    domain = urllib.parse.urlparse(url).netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain


STOP_WORDS = {
    "the", "and", "for", "are", "how", "what", "why", "can", "you", "with", "this", "that",
    "from", "about", "into", "our", "your", "their", "does", "who", "which", "when", "where",
    "will", "would", "should", "could", "give", "make", "tell", "please", "ways", "way",
}
MAX_SOURCES = 6


def prompt_keywords(query: str) -> List[str]:
    return [w.lower() for w in RE_WORDS.findall(query) if len(w) > 2 and w.lower() not in STOP_WORDS]


def relevance_score(query: str, title: str, text: str) -> int:
    """Counts how many prompt words appear in the allowed source's title, URL, or text."""
    words = prompt_keywords(query)
    if not words:
        return 1
    haystack = f"{title} {text}".lower()
    return sum(1 for w in words if w in haystack or (len(w) > 5 and w[:5] in haystack))


def build_fallback_source(url: str) -> Dict:
    domain = get_domain(url)
    path_label = (
        urllib.parse.urlparse(url).path.strip("/").replace("-", " ").replace("/", " / ")
        or "Official Page"
    )
    title = f"{domain} — {path_label}"
    return {
        "title": title,
        "url": url,
        "raw_url": url,
        "domain": domain,
        "kind": "allowed_source",
        "year": 2026,
        "trusted": True,
        "snippet": f"Official source from {domain} ({url}).",
        "excerpt": f"Official source from {domain}: {url}",
    }


def fetch_allowed_page(url: str, max_chars: int = 1200) -> Dict:
    """Fetches ONLY from one of the pre-approved ALLOWED_URLS with a strict deadline."""
    fallback_item = build_fallback_source(url)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=FAST_PAGE_TIMEOUT) as resp:
            raw_html = resp.read(FAST_MAX_PAGE_BYTES).decode("utf-8", errors="ignore")
            title_match = RE_TITLE.search(raw_html)
            page_title = clean_html_text(title_match.group(1)) if title_match else fallback_item["title"]
            body_html = RE_SCRIPT_STYLE.sub(" ", raw_html)
            text = clean_html_text(body_html)
            if not text:
                return fallback_item
            return {
                **fallback_item,
                "title": page_title or fallback_item["title"],
                "snippet": text[:250],
                "excerpt": text[:max_chars],
            }
    except Exception:
        return fallback_item


def fetch_web_documents(query: str) -> List[Dict]:
    """ONLY pulls from ALLOWED_URLS; never blocks on slow sites or external search engines."""
    allowed_set = set(ALLOWED_URLS)
    by_url: Dict[str, Dict] = {}

    futures = [FETCH_POOL.submit(fetch_allowed_page, url, 1200) for url in ALLOWED_URLS]
    try:
        for fut in as_completed(futures, timeout=BATCH_WAIT_TIMEOUT):
            try:
                item = fut.result(timeout=0.05)
                if item and item.get("url") in allowed_set:
                    by_url[item["url"]] = item
            except Exception:
                pass
    except Exception:
        pass

    fetched = [by_url.get(url) or build_fallback_source(url) for url in ALLOWED_URLS]

    scored = []
    for order, it in enumerate(fetched):
        searchable_text = f"{it.get('url', '')} {it.get('snippet', '')} {it.get('excerpt', '')}"
        score = relevance_score(query, it.get("title", ""), searchable_text)
        scored.append((score, order, it))

    matched = [it for score, _, it in scored if score > 0]
    chosen = matched if matched else [it for _, _, it in sorted(scored, key=lambda s: s[1])]
    return chosen[:MAX_SOURCES]


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = RE_FENCE.sub("", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = RE_JSON_OBJ.search(cleaned)
        if match:
            return json.loads(match.group(0))
        raise


def generate_gemini_analysis(prompt: str, sources_context: str) -> dict:
    """Uses Gemini 3.8 Flash Interactions API to summarize allowed sources and generate 4 tailored solutions."""
    if not ai_client:
        return {
            "ai_synthesis": "GEMINI_API_KEY is missing on Render. Add GEMINI_API_KEY under Render -> Environment.",
            "source_summaries": {},
            "solutions": []
        }

    context_section = (
        sources_context
        if sources_context.strip()
        else "No related records were found in the allowed official links for this prompt."
    )

    ai_prompt = f"""
You are AnInag, a civic research and problem-solving assistant. Write all output in clear, natural English.
User Prompt: "{prompt}"

Allowed Official Sources (each starts with [Source N]):
{context_section}

Rules:
- Write ONE summary for EVERY source listed above, using its exact number as "source_id".
- Each source summary is 2-3 sentences: what the page/document says, then how it relates to the User Prompt.
- Only use what the excerpt says or what is directly shown in the source. Do not invent facts or cite outside websites.

Return ONLY valid JSON (no ```json code fences) with this exact structure:
{{
  "overall_summary": "Clear, concise summary aligned to the User Prompt based strictly on the provided allowed sources.",
  "source_summaries": [
    {{
      "source_id": 1,
      "summary": "2-3 sentence summary of this source and how it relates to the User Prompt."
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

        summary_map = {}
        for item in data.get("source_summaries", []):
            try:
                source_id = int(str(item.get("source_id", "")).strip())
            except ValueError:
                continue
            if item.get("summary"):
                summary_map[source_id] = item["summary"]
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


def save_database_in_background():
    # SPEED: don't make the user wait for the disk write
    FETCH_POOL.submit(save_database, database)


def add_point(user_id: str) -> int:
    point_tag = f"points_{user_id}"
    with db_lock:
        new_points = int(database.get(point_tag, "0")) + 1
        database[point_tag] = str(new_points)
    save_database_in_background()
    return new_points


# --- 1. CHAT PROMPT, AI SUMMARIES, 4 SOLUTIONS & POINT REWARD ---
@app.post("/api/search")
def search_ngo_data(payload: SearchQuery):
    raw_prompt = payload.query.strip()
    user_id = payload.user_id.strip() if payload.user_id else "user_001"

    # +1 point per prompt, 6 points = 1 tree
    new_points = add_point(user_id)
    trees_planted = new_points // 6

    # SPEED: same prompt asked again recently? answer instantly from memory
    cache_key = raw_prompt.lower()
    cached = cache_get(cache_key)
    if cached is None:
        web_docs = fetch_web_documents(raw_prompt)

        context_chunks = []
        results = []

        for idx, doc in enumerate(web_docs, start=1):
            excerpt_text = doc["excerpt"] or doc["snippet"] or doc["title"]
            context_chunks.append(
                f"[Source {idx}] {doc['title']}\nURL: {doc['url']}\nWebsite: {doc['domain']}\nExcerpt: {excerpt_text}"
            )

            results.append({
                "id": idx,
                "title": doc["title"],
                "authors": doc["domain"],
                "year": doc["year"],
                "journal": doc["domain"],
                # shown until the AI summary replaces it
                "takeaway": (doc["snippet"] or excerpt_text)[:280],
                "ai_summarized": False,
                "citation_url": doc["url"],
                "badge": "Approved Source"
            })

        ai_output = generate_gemini_analysis(raw_prompt, "\n\n".join(context_chunks))
        ai_source_summaries = ai_output.get("source_summaries", {})

        for r in results:
            if ai_source_summaries.get(r["id"]):
                r["takeaway"] = ai_source_summaries[r["id"]]
                r["ai_summarized"] = True

        cached = {
            "ai_synthesis": ai_output.get("ai_synthesis", ""),
            "solutions": ai_output.get("solutions", []),
            "results": results
        }
        # only remember good answers (not errors)
        if cached["solutions"]:
            cache_set(cache_key, cached)

    return {
        "query": raw_prompt,
        "user_id": user_id,
        "points_earned": 1,
        "total_points": new_points,
        "trees_planted": trees_planted,
        **cached
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
    <title>AnInag AI Chat - 4 Solutions & Approved Sources</title>
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
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
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
            border-radius: 8px; outline: none; font-weight: 600; color: var(--primary); width: 130px;
        }
        .user-points-badge {
            background: var(--accent-cream); border: 1px solid var(--border); color: var(--primary);
            padding: 6px 14px; border-radius: 20px; font-weight: 700; font-size: 0.85rem;
        }
        .aninag-menu-bar {
            display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
            width: 100%; padding-top: 10px; border-top: 1px solid var(--border);
        }
        .tp-menu-link {
            text-decoration: none; font-size: 0.78rem; font-weight: 600;
            color: var(--text-main); background: var(--bg); border: 1px solid var(--border);
            padding: 5px 11px; border-radius: 999px;
            transition: background-color 0.18s ease, color 0.18s ease, border-color 0.18s ease;
        }
        .tp-menu-link:hover, .tp-menu-link:focus-visible {
            background: var(--brand); color: #FFFFFF; border-color: var(--brand); outline: none;
        }
        .tp-menu-link--account {
            background: var(--accent-cream); border-color: var(--text-muted);
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
                <input type="text" id="userIdInput" class="user-id-input" value="user_001" onchange="onUserIdChanged()">
            </div>
            <div class="user-points-badge" id="headerPointsBadge">⭐ Points: 0 | 🌳 Trees: 0</div>
            <a href="/docs" style="color:var(--text-muted); text-decoration:none; font-size:0.85rem; font-weight:600;">API Docs</a>
        </div>
        <nav class="aninag-menu-bar" aria-label="TRANSpowered Menu">
            <a href="https://aipoweredsolution-olcy.onrender.com/index.html" class="tp-menu-link" data-tp-path="index.html">Home</a>
            <a href="https://aipoweredsolution-olcy.onrender.com/condition.html" class="tp-menu-link" data-tp-path="condition.html">[01] Condition</a>
            <a href="https://aipoweredsolution-olcy.onrender.com/plant-trees.html" class="tp-menu-link" data-tp-path="plant-trees.html">[02] Plant Trees</a>
            <a href="https://aipoweredsolution-olcy.onrender.com/about-us.html" class="tp-menu-link" data-tp-path="about-us.html">[04] About Us</a>
            <a href="https://aipoweredsolution-olcy.onrender.com/progress-tracker.html" class="tp-menu-link" data-tp-path="progress-tracker.html">[05] Progress Tracker</a>
            <a href="https://aipoweredsolution-olcy.onrender.com/setup-account.html" class="tp-menu-link tp-menu-link--account" data-tp-path="setup-account.html">Set-Up Account</a>
        </nav>
    </header>

    <main class="chat-shell" id="chatThread">
        <section class="welcome-card">
            <h1>AnInag AI Chatbot</h1>
            <p>Enter a prompt to get a summary, <strong>4 tailored solutions</strong>, and insights pulled strictly from your <strong>6 approved official links</strong>. Every prompt awards <strong>+1 point</strong> (<strong>6 points = 1 tree</strong>).</p>
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

        function syncTranspoweredMenuLinks() {
            const inputEl = document.getElementById('userIdInput');
            const userId = (inputEl ? inputEl.value.trim() : '') || '';

            document.querySelectorAll('.tp-menu-link').forEach((link) => {
                const rawHref = link.getAttribute('href') || '';
                if (!rawHref) return;
                try {
                    const url = new URL(rawHref);
                    if (userId) {
                        url.searchParams.set('user_id', userId);
                    } else {
                        url.searchParams.delete('user_id');
                    }
                    link.setAttribute('href', url.toString());
                } catch (_) {
                    // Leave href unchanged if invalid
                }
            });
        }

        function initUserFromQuery() {
            const params = new URLSearchParams(window.location.search);
            const loggedOut = params.get("logged_out") === "1";
            const queryUserId = (params.get("user_id") || "").trim();
            const inputEl = document.getElementById("userIdInput");

            if (loggedOut) {
                try {
                    localStorage.removeItem("aninag_user_id");
                } catch (e) {}
                if (inputEl) {
                    inputEl.value = queryUserId || "user_001";
                }
            } else if (queryUserId && inputEl) {
                inputEl.value = queryUserId;
                try {
                    localStorage.setItem("aninag_user_id", queryUserId);
                } catch (e) {}
            } else if (inputEl) {
                try {
                    const savedId = localStorage.getItem("aninag_user_id");
                    if (savedId) inputEl.value = savedId;
                } catch (e) {}
            }

            syncTranspoweredMenuLinks();
            fetchUserPoints();
        }

        function onUserIdChanged() {
            const inputEl = document.getElementById("userIdInput");
            const userId = (inputEl ? inputEl.value.trim() : "") || "user_001";
            if (inputEl) inputEl.value = userId;
            try {
                localStorage.setItem("aninag_user_id", userId);
            } catch (e) {}
            syncTranspoweredMenuLinks();
            fetchUserPoints();
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
            loadingCard.innerHTML = `<div style="color:var(--brand); font-weight:600;">AnInag Chatbot is thinking...</div>`;
            thread.appendChild(loadingCard);
            loadingCard.scrollIntoView({ behavior: 'smooth', block: 'end' });

            try {
                const response = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query, user_id: userId })
                });
                const rawText = await response.text();
                let data;
                try {
                    data = JSON.parse(rawText);
                } catch (_) {
                    throw new Error(`Server returned HTTP ${response.status}`);
                }
                if (!response.ok) {
                    throw new Error(data.detail || `Server error HTTP ${response.status}`);
                }

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
                                <div style="font-size:0.74rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">${res.ai_summarized ? 'AI Summary' : 'Preview'}</div>
                                <div style="font-size:0.92rem; margin-top:4px; line-height:1.5; color:var(--primary);">${escapeHtml(res.takeaway)}</div>
                            </div>
                            <a href="${escapeHtml(res.citation_url)}" target="_blank" class="source-link">🔗 Open approved source (${escapeHtml(res.journal)}) ↗</a>
                        </div>
                    `).join('')
                    : `<div class="empty-sources-box">No matching records could be found in the approved sources right now.</div>`;

                loadingCard.innerHTML = `
                    <div class="bot-header">
                        <div class="bot-title">AI Summary, 4 Solutions & Approved Sources</div>
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
                        <div class="section-heading">Approved Sources Used</div>
                        ${sourcesHtml}
                    </div>
                `;
                loadingCard.scrollIntoView({ behavior: 'smooth', block: 'end' });
            } catch (err) {
                loadingCard.innerHTML = `<div style="color:#8F4E3E; font-weight:600;">Error communicating with backend server (${escapeHtml(err.message || 'request failed')}).</div>`;
            }
        }

        document.addEventListener("DOMContentLoaded", initUserFromQuery);
    </script>
</body>
</html>"""
