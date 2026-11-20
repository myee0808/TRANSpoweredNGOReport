from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from fastapi.middleware.gzip import GZipMiddleware
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from pydantic import BaseModel
from google import genai
import urllib.request
import urllib.parse
import threading
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

# SPEED: compile regexes once instead of on every request
RE_WORDS = re.compile(r"[a-zA-Z0-9]+")
RE_SCRIPT_STYLE = re.compile(r"<(script|style).*?>.*?</\1>", re.S | re.I)
RE_TAGS = re.compile(r"<[^<]+?>")
RE_SPACES = re.compile(r"\s+")
RE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)
RE_JSON_OBJ = re.compile(r"\{.*\}", re.S)

# SPEED: one shared pool for fetching pages in parallel
FETCH_POOL = ThreadPoolExecutor(max_workers=8)

# SPEED: only read the first part of each web page (the text we need is near the top)
MAX_PAGE_BYTES = 150_000


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


def fetch_page_excerpt(url: str, max_chars: int = 1200) -> str:
    """Fetches a short text excerpt from a page so AI can summarize it."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            raw_html = resp.read(MAX_PAGE_BYTES).decode("utf-8", errors="ignore")
            raw_html = RE_SCRIPT_STYLE.sub(" ", raw_html)
            text = RE_TAGS.sub(" ", raw_html)
            text = RE_SPACES.sub(" ", text).strip()
            return text[:max_chars]
    except Exception:
        return ""


# --- LOOSER SEARCH: find articles/documents RELATED to the prompt ---
SEARCH_URLS = [
    "https://html.duckduckgo.com/html/?q={q}",   # main search
    "https://lite.duckduckgo.com/lite/?q={q}",   # backup if the main one fails
]
RE_DDG_LINK = re.compile(r'<a[^>]+class=["\'](?:result__a|result-link)["\'][^>]*>.*?</a>', re.S)
RE_HREF = re.compile(r'href=["\']([^"\']+)["\']')
RE_DDG_SNIPPET = re.compile(r'class=["\'](?:result__snippet|result-snippet)["\'][^>]*>(.*?)</(?:a|td)>', re.S)
TRUSTED_ENDINGS = (".gov.ph", ".gov", ".edu.ph", ".edu", ".org", ".org.ph", "lawphil.net", ".int")
STOP_WORDS = {
    "the", "and", "for", "are", "how", "what", "why", "can", "you", "with", "this", "that",
    "from", "about", "into", "our", "your", "their", "does", "who", "which", "when", "where",
    "will", "would", "should", "could", "give", "make", "tell", "please", "ways", "way",
}
MAX_SOURCES = 5


def clean_html_text(text: str) -> str:
    import html as html_lib
    return RE_SPACES.sub(" ", html_lib.unescape(RE_TAGS.sub(" ", text))).strip()


def get_domain(url: str) -> str:
    domain = urllib.parse.urlparse(url).netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain


def is_trusted(url: str) -> bool:
    return get_domain(url).endswith(TRUSTED_ENDINGS)


def prompt_keywords(query: str) -> List[str]:
    return [w.lower() for w in RE_WORDS.findall(query) if len(w) > 2 and w.lower() not in STOP_WORDS]


def relevance_score(query: str, title: str, text: str) -> int:
    """LOOSE matching: counts how many prompt words appear (also matches 'flood' with 'flooding')."""
    words = prompt_keywords(query)
    if not words:
        return 1
    haystack = f"{title} {text}".lower()
    return sum(1 for w in words if w in haystack or (len(w) > 5 and w[:5] in haystack))


def search_web(query: str, limit: int = 8) -> List[Dict]:
    """Returns [{url, title, snippet}] from DuckDuckGo (no API key needed)."""
    for template in SEARCH_URLS:
        url = template.format(q=urllib.parse.quote(query))
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=4) as response:
                page = response.read().decode("utf-8", errors="ignore")
        except Exception:
            continue

        links = RE_DDG_LINK.findall(page)
        snippets = [clean_html_text(s) for s in RE_DDG_SNIPPET.findall(page)]
        found = []
        for i, link_html in enumerate(links):
            href_match = RE_HREF.search(link_html)
            if not href_match:
                continue
            href = href_match.group(1).replace("&amp;", "&")
            uddg = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
            real_url = uddg[0] if uddg else href
            if not real_url.startswith("http") or "duckduckgo.com" in real_url:
                continue  # skip ads / internal links
            found.append({
                "url": real_url,
                "title": clean_html_text(link_html),
                "snippet": snippets[i] if i < len(snippets) else "",
            })
            if len(found) >= limit:
                break
        if found:
            return found
    return []


# --- BACKUP SOURCES (free, no API key, don't block you like web search does) ---
API_HEADERS = {"User-Agent": "AnInag/1.0 (civic research chatbot)"}
_web_search_paused_until = 0.0  # web search said "too many searches" -> rest it for a bit


def get_json(url: str, timeout: float = 4):
    req = urllib.request.Request(url, headers=API_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def search_research_papers(query: str, limit: int = 3) -> List[Dict]:
    """Research papers/documents with abstracts from OpenAlex."""
    url = (
        "https://api.openalex.org/works?search=" + urllib.parse.quote(query)
        + f"&per_page={limit}&filter=has_abstract:true"
        + "&select=title,doi,id,publication_year,abstract_inverted_index,primary_location"
    )
    try:
        data = get_json(url)
    except Exception:
        return []
    papers = []
    for work in data.get("results", []):
        inverted = work.get("abstract_inverted_index") or {}
        positions = sorted((p, word) for word, plist in inverted.items() for p in plist)
        abstract = " ".join(word for _, word in positions)
        link = work.get("doi") or (work.get("primary_location") or {}).get("landing_page_url") or work.get("id")
        if not link or not work.get("title") or len(abstract) < 80:
            continue
        papers.append({
            "url": link,
            "title": clean_html_text(work["title"]),
            "snippet": abstract[:300],
            "excerpt": abstract[:2000],
            "kind": "research",
            "year": work.get("publication_year") or 2026,
            "source_name": ((work.get("primary_location") or {}).get("source") or {}).get("display_name"),
        })
    return papers


def search_wikipedia(query: str, limit: int = 3) -> List[Dict]:
    """Last-resort backup so the user always gets something related."""
    url = (
        "https://en.wikipedia.org/w/api.php?action=query&list=search&format=json"
        f"&srlimit={limit}&srsearch=" + urllib.parse.quote(query)
    )
    try:
        data = get_json(url)
    except Exception:
        return []
    pages = []
    for item in data.get("query", {}).get("search", []):
        title = item.get("title", "")
        pages.append({
            "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            "title": title,
            "snippet": clean_html_text(item.get("snippet", "")),
            "kind": "encyclopedia",
        })
    return pages


def fetch_web_documents(query: str) -> List[Dict]:
    """Finds articles/documents RELATED to the prompt (not strict) and reads a bit of each."""
    global _web_search_paused_until

    # Search the web AND research papers at the same time
    use_web = time.time() >= _web_search_paused_until
    web_future = FETCH_POOL.submit(search_web, query, 8) if use_web else None
    papers_future = FETCH_POOL.submit(search_research_papers, query, 3)

    web_items: List[Dict] = []
    if web_future:
        try:
            web_items = web_future.result(timeout=6)
        except Exception:
            web_items = []
        if not web_items:
            _web_search_paused_until = time.time() + 60  # probably "too many searches"
    try:
        papers = papers_future.result(timeout=6)
    except Exception:
        papers = []

    for item in web_items:
        item["kind"] = "web"

    # Read the web pages AT THE SAME TIME (research papers already come with their abstract)
    if web_items:
        futures = {FETCH_POOL.submit(fetch_page_excerpt, w["url"], 2000): w for w in web_items}
        try:
            for fut in as_completed(futures, timeout=3.5):
                futures[fut]["page_text"] = fut.result()
        except Exception:
            pass  # slow pages just use the search snippet

    # Too few results? Add Wikipedia so the user still gets related reading
    encyclopedia = search_wikipedia(query, 3) if len(web_items) + len(papers) < 3 else []

    def ranked(items: List[Dict]) -> List[Dict]:
        scored = []
        for order, it in enumerate(items):
            excerpt = it.get("excerpt") or f"{it.get('snippet', '')} {it.get('page_text', '')}".strip()
            it["excerpt"] = excerpt[:2000]
            score = relevance_score(query, it["title"], excerpt)
            if is_trusted(it["url"]) or it["kind"] == "research":
                score += 1  # small bonus for .gov / .edu / .org / research
            scored.append((score, order, it))
        # Best matches first, but NEVER drop everything: the search already judged these related
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [it for _, _, it in scored]

    web_ranked, papers_ranked = ranked(web_items), ranked(papers)
    # Wikipedia is only a filler, so skip pages that share no words with the prompt
    wiki_ranked = [w for w in ranked(encyclopedia) if relevance_score(query, w["title"], w["excerpt"]) > 0]

    # Mix: up to 3 articles + up to 2 research papers, then fill up to 5 with whatever is left
    chosen = web_ranked[:3] + papers_ranked[:2]
    leftovers = web_ranked[3:] + papers_ranked[2:] + wiki_ranked
    chosen += leftovers[: max(0, MAX_SOURCES - len(chosen))]

    seen, results = set(), []
    for it in chosen:
        key = it["url"].split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "title": it["title"] or get_domain(it["url"]),
            "url": it["url"],
            "raw_url": it["url"],
            "domain": it.get("source_name") or get_domain(it["url"]),
            "kind": it["kind"],
            "year": it.get("year", 2026),
            "trusted": is_trusted(it["url"]) or it["kind"] == "research",
            "snippet": it.get("snippet", ""),
            "excerpt": it["excerpt"],
        })
    return results


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
        else "No related articles were retrieved for this prompt. Answer from general knowledge."
    )

    ai_prompt = f"""
You are AnInag, a civic research and problem-solving assistant. Write all output in clear, natural English.
User Prompt: "{prompt}"

Related Articles / Documents (each starts with [Source N]):
{context_section}

Rules:
- Write ONE summary for EVERY source listed above, using its exact number as "source_id".
- Each source summary is 2-3 sentences: what the article/document says, then how it relates to the User Prompt.
- Only use what the excerpt says. If the excerpt is short, summarize what is there and do not invent facts.

Return ONLY valid JSON (no ```json code fences) with this exact structure:
{{
  "overall_summary": "Clear, concise summary aligned to the User Prompt based on the provided information.",
  "source_summaries": [
    {{
      "source_id": 1,
      "summary": "2-3 sentence summary of this article and how it relates to the User Prompt."
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
                f"[Source {idx}] {doc['title']}\nWebsite: {doc['domain']}\nExcerpt: {excerpt_text}"
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
                "badge": {"research": "Research Paper", "encyclopedia": "Encyclopedia"}.get(
                    doc["kind"], "Trusted Source" if doc["trusted"] else "Related Article"
                )
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
    <title>AnInag AI Chat - 4 Solutions & Related Articles</title>
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
            <p>Enter a prompt to get a summary, <strong>4 tailored solutions</strong>, and each source is carefully selected to ensure credibility. Every prompt awards <strong>+1 point</strong> (<strong>6 points = 1 tree</strong>) synced with MIT App Inventor.</p>
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
            loadingCard.innerHTML = `<div style="color:var(--brand); font-weight:600;">Finding related articles and writing AI summaries...</div>`;
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
                                <div style="font-size:0.74rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">${res.ai_summarized ? 'AI Summary' : 'Preview'}</div>
                                <div style="font-size:0.92rem; margin-top:4px; line-height:1.5; color:var(--primary);">${escapeHtml(res.takeaway)}</div>
                            </div>
                            <a href="${escapeHtml(res.citation_url)}" target="_blank" class="source-link">🔗 Read the full article (${escapeHtml(res.journal)}) ↗</a>
                        </div>
                    `).join('')
                    : `<div class="empty-sources-box">No related articles could be found right now. Try adding more detail to your prompt.</div>`;

                loadingCard.innerHTML = `
                    <div class="bot-header">
                        <div class="bot-title">AI Summary, 4 Solutions & Related Articles</div>
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
                        <div class="section-heading">Related Articles & Documents</div>
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
    with db_lock:
        database[tag] = value
    save_database_in_background()
    return ["stored", tag, value]


@app.post("/getvalue")
def get_val(tag: str = Form(...)):
    value = database.get(tag, "0")
    return [tag, value]
