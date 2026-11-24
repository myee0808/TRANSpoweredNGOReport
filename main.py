from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
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
import traceback

app = FastAPI(title="AnInag AI Research & 4-Solution Chat")
app.add_middleware(GZipMiddleware, minimum_size=1000)

DB_FILE = "database.json"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
print("[AnInag] loading app...", flush=True)

_ai_client = None
_ai_client_lock = threading.Lock()


def get_ai_client():
    global _ai_client
    if _ai_client is None and GEMINI_API_KEY:
        with _ai_client_lock:
            if _ai_client is None:
                try:
                    from google.genai import types as genai_types
                    _ai_client = genai.Client(
                        api_key=GEMINI_API_KEY,
                        http_options=genai_types.HttpOptions(
                            retry_options=genai_types.HttpRetryOptions(attempts=1),
                        ),
                    )
                except Exception:
                    _ai_client = genai.Client(api_key=GEMINI_API_KEY)
    return _ai_client


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# STRICT ALLOWLIST: AnInag will ONLY cite these exact URLs, tagged by topic so AI picks only relevant ones
ALLOWED_SOURCE_CATALOG: List[Dict[str, str]] = [
    {
        "url": "https://www.officialgazette.gov.ph/2026/10/13/executive-order-no-127-s-2026/",
        "topics": "environment laws, climate change, carbon market policy, DENR, nature protection, green rules, environmental regulations",
    },
    {
        "url": "https://www.officialgazette.gov.ph/section/republic-acts/",
        "topics": "republic acts, national laws, health policies, healthcare regulations, public safety laws",
    },
    {
        "url": "https://lawphil.net/statutes/repacts/repacts.html",
        "topics": "full text laws, statutes, republic acts, health and environmental regulations",
    },
    {
        "url": "https://www.congress.gov.ph/legislative-documents",
        "topics": "house bills, legislative documents, health and environment proposals",
    },
    {
        "url": "https://ldr.senate.gov.ph/subject/government-documents",
        "topics": "senate documents, official government records, policy documents",
    },
    {
        "url": "https://ppp.gov.ph/project-database/",
        "topics": "public-private projects, infrastructure, clinics, environmental and community projects",
    },
    {
        "url": "https://www.coa.gov.ph/FAQS/is-there-a-manual-or-list-of-documentary-requirements-for-common-government-transactions/",
        "topics": "audit rules, government requirements, documentary checklists, public funds",
    },
]

ALLOWED_URLS: List[str] = [item["url"] for item in ALLOWED_SOURCE_CATALOG]
CATALOG_BY_URL: Dict[str, Dict[str, str]] = {item["url"]: item for item in ALLOWED_SOURCE_CATALOG}

RE_WORDS = re.compile(r"[a-zA-Z0-9]+")
RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
RE_SCRIPT_STYLE = re.compile(r"<(script|style).*?>.*?</\1>", re.S | re.I)
RE_TAGS = re.compile(r"<[^<]+?>")
RE_SPACES = re.compile(r"\s+")
RE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)
RE_JSON_OBJ = re.compile(r"\{.*\}", re.S)

FETCH_POOL = ThreadPoolExecutor(max_workers=6)
SAVE_POOL = ThreadPoolExecutor(max_workers=1)

GEMINI_TIMEOUT_SECONDS = 28

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
]
_dead_models = set()
GEMINI_TOTAL_BUDGET_SECONDS = 55
_last_good_model = {"name": None, "until": 0.0}
_model_lock = threading.Lock()

GEMINI_THINKING_LEVEL = "low"
GEMINI_CALL_POOL = ThreadPoolExecutor(max_workers=16)

PAGE_REFRESH_SECONDS = 30 * 60
EXCERPT_CHARS = 700

FAST_PAGE_TIMEOUT = 1.6
BACKGROUND_PAGE_TIMEOUT = 10
FAST_MAX_PAGE_BYTES = 80_000

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


CACHE_TTL_SECONDS = 60 * 30
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
MAX_CANDIDATE_SOURCES = 6


def prompt_keywords(query: str) -> List[str]:
    return [w.lower() for w in RE_WORDS.findall(query) if len(w) > 2 and w.lower() not in STOP_WORDS]


def relevance_score(query: str, title: str, text: str, topic_hint: str = "") -> int:
    words = prompt_keywords(query)
    if not words:
        return 1
    haystack = f"{title} {topic_hint} {text}".lower()
    return sum(1 for w in words if w in haystack or (len(w) > 5 and w[:5] in haystack))


def build_fallback_source(url: str) -> Dict:
    domain = get_domain(url)
    path_label = (
        urllib.parse.urlparse(url).path.strip("/").replace("-", " ").replace("/", " / ")
        or "Official Page"
    )
    topic_hint = CATALOG_BY_URL.get(url, {}).get("topics", "")
    title = f"{domain} — {path_label}"
    return {
        "title": title,
        "url": url,
        "raw_url": url,
        "domain": domain,
        "kind": "allowed_source",
        "year": 2026,
        "trusted": True,
        "topic_hint": topic_hint,
        "snippet": f"Official source from {domain} ({url}).",
        "excerpt": f"Official source from {domain}: {url}",
    }


def fetch_allowed_page(url: str, max_chars: int = 1200, timeout: float = FAST_PAGE_TIMEOUT) -> Dict:
    fallback_item = build_fallback_source(url)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


_page_cache: Dict[str, Dict] = {}
_page_cache_lock = threading.Lock()


def refresh_allowed_pages():
    futures = {FETCH_POOL.submit(fetch_allowed_page, url, EXCERPT_CHARS, BACKGROUND_PAGE_TIMEOUT): url for url in ALLOWED_URLS}
    for fut in as_completed(futures):
        url = futures[fut]
        try:
            item = fut.result()
        except Exception:
            item = None
        if item:
            with _page_cache_lock:
                old = _page_cache.get(url)
                failed_now = item.get("excerpt", "").startswith("Official source from")
                had_good = old and not old.get("excerpt", "").startswith("Official source from")
                if failed_now and had_good:
                    continue
                _page_cache[url] = item


def _page_refresher_loop():
    time.sleep(3)
    while True:
        try:
            refresh_allowed_pages()
            print(f"[AnInag] approved pages refreshed ({len(_page_cache)} in memory)", flush=True)
        except Exception as e:
            print(f"[AnInag] page refresh error: {e!r}", flush=True)
        time.sleep(PAGE_REFRESH_SECONDS)


@app.on_event("startup")
def start_page_refresher():
    threading.Thread(target=_page_refresher_loop, daemon=True).start()
    print("[AnInag] server started, Render can now open the site", flush=True)


def fetch_web_documents(query: str) -> List[Dict]:
    """Reads all 6 approved pages from memory with their topic hints so AI can filter strictly."""
    with _page_cache_lock:
        fetched = [_page_cache.get(url) or build_fallback_source(url) for url in ALLOWED_URLS]

    scored = []
    for order, it in enumerate(fetched):
        topic_hint = it.get("topic_hint") or CATALOG_BY_URL.get(it.get("url", ""), {}).get("topics", "")
        it_with_hint = {**it, "topic_hint": topic_hint}
        searchable_text = f"{it.get('url', '')} {it.get('snippet', '')} {it.get('excerpt', '')}"
        score = relevance_score(query, it.get("title", ""), searchable_text, topic_hint)
        scored.append((score, order, it_with_hint))

    scored.sort(key=lambda s: (-s[0], s[1]))
    return [it for _, _, it in scored[:MAX_CANDIDATE_SOURCES]]


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


class GeminiBusyError(Exception):
    """Every Gemini model we tried was busy or unavailable."""


def is_busy_error(e: Exception) -> bool:
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    if code in (404, 429, 500, 502, 503, 504):
        return True
    text = f"{type(e).__name__} {e}".lower()
    return any(k in text for k in (
        "503", "429", "high demand", "overloaded", "unavailable", "resource_exhausted",
        "rate limit", "try again later", "timed out", "timeout", "not found", "404",
    ))


def models_to_try() -> List[str]:
    with _model_lock:
        good = _last_good_model["name"] if time.time() < _last_good_model["until"] else None
        dead = set(_dead_models)
    alive = [m for m in GEMINI_MODELS if m not in dead] or list(GEMINI_MODELS)
    if good and good in alive:
        alive = [good] + [m for m in alive if m != good]
    return alive + alive


def is_missing_model_error(e: Exception) -> bool:
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    text = f"{type(e).__name__} {e}".lower()
    return code == 404 or "notfound" in text or "no longer available" in text or "not found" in text


def build_gemini_gen_config(max_tokens: int) -> dict:
    cfg = {
        "max_output_tokens": max_tokens,
        "temperature": 0.35,
    }
    


def extract_text_from_response(resp) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text

    direct_text = getattr(resp, "text", None)
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text

    outputs = getattr(resp, "outputs", None)
    if isinstance(outputs, list):
        chunks = []
        for item in outputs:
            val = getattr(item, "text", None)
            if isinstance(val, str) and val.strip():
                chunks.append(val)
            elif isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
                chunks.append(item["text"])
        if chunks:
            return "\n".join(chunks)

    candidates = getattr(resp, "candidates", None)
    if isinstance(candidates, list):
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) if content else None
            if isinstance(parts, list):
                txt = "".join(getattr(p, "text", "") or "" for p in parts).strip()
                if txt:
                    return txt
    return ""


def call_gemini_model(model_name: str, prompt: str, max_tokens: int):
    client = get_ai_client()
    cfg = build_gemini_gen_config(max_tokens)
    search_tools = [{"type": "google_search"}]
    if hasattr(client, "interactions"):
        try:
            return client.interactions.create(
                model=model_name,
                input=prompt,
                tools=search_tools,
                generation_config=cfg,
                store=False,
            )
        except TypeError:
            try:
                return client.interactions.create(
                    model=model_name,
                    input=prompt,
                    generation_config=cfg,
                    store=False,
                )
            except TypeError:
                return client.interactions.create(
                    model=model_name,
                    input=prompt,
                )
    return client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={"max_output_tokens": max_tokens, "temperature": 0.35},
    )


def ask_gemini_json(ai_prompt: str, max_tokens: int) -> dict:
    started = time.time()
    last_error: Optional[Exception] = None
    for _, model_name in enumerate(models_to_try()):
        remaining = GEMINI_TOTAL_BUDGET_SECONDS - (time.time() - started)
        if remaining < 4:
            break
        try:
            per_call = max(4, min(GEMINI_TIMEOUT_SECONDS, remaining))
            future = GEMINI_CALL_POOL.submit(call_gemini_model, model_name, ai_prompt, max_tokens)
            try:
                resp = future.result(timeout=per_call + 1)
            except FutureTimeout:
                raise TimeoutError(f"{model_name} timed out after {per_call:.0f}s")
            raw_output = extract_text_from_response(resp)
            data = extract_json_object(raw_output)
            if model_name != GEMINI_MODELS[0]:
                print(f"[AnInag] used backup model {model_name}", flush=True)
                with _model_lock:
                    _last_good_model.update(name=model_name, until=time.time() + 180)
            elif _last_good_model["name"]:
                with _model_lock:
                    _last_good_model.update(name=None, until=0.0)
            return data
        except Exception as e:
            last_error = e
            print(f"[AnInag] {model_name} failed: {type(e).__name__}: {str(e)[:200]}", flush=True)
            if not is_busy_error(e) and not is_missing_model_error(e):
                raise
            time.sleep(0.3)
    raise GeminiBusyError(str(last_error) if last_error else "no model answered in time")


def generate_gemini_analysis(prompt: str, sources_context: str) -> dict:
    if not get_ai_client():
        return {
            "ai_synthesis": "GEMINI_API_KEY is missing on Render. Add GEMINI_API_KEY under Render -> Environment.",
            "selected_source_ids": [],
            "source_summaries": {},
            "solutions": [],
        }

    context_section = (
        sources_context.strip()
        if sources_context and sources_context.strip()
        else "No related records were found in the allowed official links for this prompt."
    )

    ai_prompt = f"""You are AnInag, a helpful civic and policy guide for the Philippines.
User Prompt: "{prompt}"

Reference Context from Approved Catalog:
{context_section}

INSTRUCTIONS:
1. Give a rich, clear, substantial explanation of actual Philippine laws, rules, policies, or government programs that directly match the User Prompt. Do NOT give repetitive or vague answers—mention real, specific Philippine policies or regulations (such as specific Republic Acts, Executive Orders, or agency rules) that apply to the user's topic.
2. Write in a warm, clear style that a curious 12-to-18-year-old can easily understand:
    Use simple everyday words and short, friendly sentences.
    Avoid heavy legal jargon or big complicated terms; whenever you mention a law or rule, explain right away in simple words what it does to help people or nature.
3. For each specific law, rule, or policy you mention, include its real, accessible URL (preferring official Philippine government or law pages when available, and never guessing or making up broken URLs) under "specific_policies".
4. Give 4 concrete, varied, practical solutions tailored to the User Prompt and those real Philippine rules, also written in simple 12-to-18-year-old language.

Return ONLY valid JSON (no ```json fences) with this exact structure:
{{
  "overall_summary": "5-7 clear sentences for a 12-to-18-year-old explaining the key Philippine rules/policies for this topic and why they matter.",
  "specific_policies": [
    {{
      "title": "Exact name of the specific Philippine law, order, or policy",
      "agency_or_type": "Republic Act / Executive Order / Agency Rule",
      "summary": "5-7 simple sentences explaining what this specific rule does in friendly words.",
      "url": "https://..."
    }}
  ],
  "solutions": [
    {{
      "number": "Solution 1",
      "title": "Short clear title",
      "focus": "Home / School / Community / Official Rule",
      "description": "3 simple, practical sentences for a 12-to-18-year-old."
    }},
    {{
      "number": "Solution 2",
      "title": "Short clear title",
      "focus": "Home / School / Community / Official Rule",
      "description": "3 simple, practical sentences for a 12-to-18-year-old."
    }},
    {{
      "number": "Solution 3",
      "title": "Short clear title",
      "focus": "Home / School / Community / Official Rule",
      "description": "3 simple, practical sentences for a 12-to-18-year-old."
    }},
    {{
      "number": "Solution 4",
      "title": "Short clear title",
      "focus": "Home / School / Community / Official Rule",
      "description": "3 clear, practical sentences for a 12-to-18-year-old."
    }}
  ]
}}
"""

    try:
        data = ask_gemini_json(ai_prompt, max_tokens=2800)

        selected_ids: List[int] = []
        for raw_id in data.get("selected_source_ids", []) or []:
            try:
                sid = int(str(raw_id).strip())
                if sid not in selected_ids:
                    selected_ids.append(sid)
            except ValueError:
                continue
        summary_map: Dict[int, str] = {}
        for item in data.get("source_summaries", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                source_id = int(str(item.get("source_id", "")).strip())
            except ValueError:
                continue
            if item.get("summary"):
                summary_map[source_id] = str(item["summary"]).strip()
        if not selected_ids and summary_map:
            selected_ids = list(summary_map.keys())

        raw_solutions = data.get("solutions", []) or []
        clean_solutions = []
        for idx, s in enumerate(raw_solutions[:4], start=1):
            if isinstance(s, dict):
                clean_solutions.append({
                    "number": str(s.get("number") or f"Solution {idx}").strip(),
                    "title": str(s.get("title") or f"Helpful Idea {idx}").strip(),
                    "focus": str(s.get("focus") or "Action Step").strip(),
                    "description": str(s.get("description") or "").strip(),
                })

        return {
            "ai_synthesis": str(data.get("overall_summary", "")).strip() or "Here is a simple summary of what we found.",
            "selected_source_ids": selected_ids,
            "source_summaries": summary_map,
            "solutions": clean_solutions,
        }
    except Exception as e:
        print(f"[ANINAG_GEMINI_ERR] {type(e).__name__}: {e}", flush=True)
        err_detail = str(e).strip() or type(e).__name__
        print(f"[AnInag] Gemini final error ({type(e).__name__}): {err_detail}", flush=True)
        friendly = f"AI summarization error ({type(e).__name__}): {err_detail}"
        return {
            "ai_synthesis": friendly,
            "selected_source_ids": [],
            "source_summaries": {},
            "solutions": [],
        }

def save_database_in_background():
    SAVE_POOL.submit(save_database, database)


def add_point(user_id: str) -> int:
    point_tag = f"points_{user_id}"
    with db_lock:
        new_points = int(database.get(point_tag, "0")) + 1
        database[point_tag] = str(new_points)
    save_database_in_background()
    return new_points


def run_search(payload: SearchQuery):
    raw_prompt = (payload.query or "").strip()
    user_id = payload.user_id.strip() if payload.user_id else "user_001"
    if not raw_prompt:
        return JSONResponse(status_code=400, content={"detail": "Please type a prompt first."})

    new_points = add_point(user_id)
    trees_planted = new_points // 6

    cache_key = raw_prompt.lower()
    cached = cache_get(cache_key)
    if cached is None:
        web_docs = fetch_web_documents(raw_prompt)
        context_chunks = []
        candidate_by_id: Dict[int, Dict] = {}

        for idx, doc in enumerate(web_docs, start=1):
            excerpt_text = doc.get("excerpt") or doc.get("snippet") or doc.get("title", "")
            topic_hint = doc.get("topic_hint") or ""
            context_chunks.append(
                f"[Source {idx}] {doc['title']}\nURL: {doc['url']}\nWebsite: {doc['domain']}\nTopics Covered: {topic_hint}\nExcerpt: {excerpt_text}"
            )
            candidate_by_id[idx] = doc

        ai_output = generate_gemini_analysis(raw_prompt, "\n\n".join(context_chunks))
        selected_ids = ai_output.get("selected_source_ids", [])
        ai_source_summaries = ai_output.get("source_summaries", {})

        # Keep ONLY the sources Gemini selected for this topic
        filtered_results = []
        for sid in selected_ids:
            doc = candidate_by_id.get(sid)
            if not doc:
                continue
            summary_text = ai_source_summaries.get(sid) or (doc.get("snippet") or doc.get("title", ""))[:280]
            filtered_results.append({
                "id": sid,
                "title": doc["title"],
                "authors": doc["domain"],
                "year": doc.get("year", 2026),
                "journal": doc["domain"],
                "takeaway": summary_text,
                "ai_summarized": bool(ai_source_summaries.get(sid)),
                "citation_url": doc["url"],
                "badge": "Matched Source",
            })

        cached = {
            "ai_synthesis": ai_output.get("ai_synthesis", ""),
            "solutions": ai_output.get("solutions", []),
            "results": filtered_results,
        }
        if cached["solutions"]:
            cache_set(cache_key, cached)

    return {
        "query": raw_prompt,
        "user_id": user_id,
        "points_earned": 1,
        "total_points": new_points,
        "trees_planted": trees_planted,
        **cached,
    }


@app.post("/api/search")
def search_ngo_data(payload: SearchQuery):
    try:
        return run_search(payload)
    except Exception as e:
        print("[AnInag] /api/search crashed:\n" + traceback.format_exc(), flush=True)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Backend error: {type(e).__name__}: {e}"},
        )


@app.get("/api/health")
def health_check():
    return {
        "server": "ok",
        "gemini_key_set": bool(GEMINI_API_KEY),
        "saved_users": sum(1 for k in database if k.startswith("points_")),
    }


@app.get("/api/points/{user_id}")
def get_user_points(user_id: str):
    points = int(database.get(f"points_{user_id}", "0"))
    trees = points // 6
    return {"user_id": user_id, "points": points, "trees": trees}


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
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            cursor: pointer;
            transition: border-color 0.18s ease, transform 0.15s ease, box-shadow 0.18s ease;
        }
        .solution-card:hover {
            border-color: var(--brand);
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(91, 58, 41, 0.08);
        }
        .solution-cta {
            margin-top: 6px;
            font-size: 0.78rem;
            font-weight: 700;
            color: var(--brand-dark);
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
            <p>Enter a prompt to get a kid-friendly summary, <strong>4 tailored solutions</strong>, and only the <strong>matching official links</strong> from our 6 approved sources. Every prompt awards <strong>+1 point</strong> (<strong>6 points = 1 tree</strong>).</p>
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
                } catch (_) {}
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
                if (inputEl) inputEl.value = "user_001";
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
                    if (response.status === 502 || response.status === 503 || response.status === 504) {
                        throw new Error(`Server returned HTTP ${response.status}. The server is waking up or restarting. Wait about 30 seconds and send your prompt again`);
                    }
                    throw new Error(`Server returned HTTP ${response.status}`);
                }
                if (!response.ok) {
                    throw new Error(data.detail || `Server error HTTP ${response.status}`);
                }

                document.getElementById('headerPointsBadge').innerText = `⭐ Points: ${data.total_points} | 🌳 Trees: ${data.trees_planted}`;

                const cardIndex = document.querySelectorAll('.bot-card').length;
                window.__aninagSolutions = window.__aninagSolutions || {};
                window.__aninagSolutions[cardIndex] = data.solutions || [];

                const solutionsHtml = (data.solutions || []).map((sol, idx) => `
                    <div class="solution-card" role="button" tabindex="0"
                         onclick="selectSolutionForTracker(${cardIndex}, ${idx})"
                         onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectSolutionForTracker(${cardIndex}, ${idx}); }">
                        <span class="solution-badge">${escapeHtml(sol.number)}</span>
                        <div class="solution-title">${escapeHtml(sol.title)}</div>
                        <div class="solution-focus">${escapeHtml(sol.focus)}</div>
                        <div class="solution-desc">${escapeHtml(sol.description)}</div>
                        <div class="solution-cta">Add to Progress Tracker -&gt;</div>
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
                                <div style="font-size:0.74rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">${res.ai_summarized ? 'Kid-Friendly Summary' : 'Preview'}</div>
                                <div style="font-size:0.92rem; margin-top:4px; line-height:1.5; color:var(--primary);">${escapeHtml(res.takeaway)}</div>
                            </div>
                            <a href="${escapeHtml(res.citation_url)}" target="_blank" class="source-link">🔗 Open approved source (${escapeHtml(res.journal)}) ↗</a>
                        </div>
                    `).join('')
                    : `<div class="empty-sources-box">No matching links from the 6 approved sources fit this specific topic.</div>`;

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
                        <div class="solutions-grid">${solutionsHtml || '<div style="font-size:0.9rem; color:var(--text-muted);">No solutions this time — the AI was busy. Please send your prompt again in a minute.</div>'}</div>
                    </div>
                    <div>
                        <div class="section-heading">Matched Approved Sources</div>
                        ${sourcesHtml}
                    </div>
                `;
                loadingCard.scrollIntoView({ behavior: 'smooth', block: 'end' });
            } catch (err) {
                loadingCard.innerHTML = `<div style="color:#8F4E3E; font-weight:600;">Error communicating with backend server (${escapeHtml(err.message || 'request failed')}).</div>`;
            }
        }

        document.addEventListener("DOMContentLoaded", initUserFromQuery);

        function selectSolutionForTracker(cardIndex, solIndex) {
            const list = (window.__aninagSolutions && window.__aninagSolutions[cardIndex]) || [];
            const sol = list[solIndex];
            if (!sol) return;

            const userId = (document.getElementById('userIdInput')?.value.trim()) || 'user_001';
            const trackerUrl = new URL('https://aipoweredsolution-olcy.onrender.com/progress-tracker.html');

            if (userId && userId !== 'user_001') {
                trackerUrl.searchParams.set('user_id', userId);
            }
            trackerUrl.searchParams.set('new_task_title', sol.title || sol.number || 'Selected Solution');
            trackerUrl.searchParams.set('new_task_focus', sol.focus || 'AnInag Action Item');
            trackerUrl.searchParams.set('new_task_desc', sol.description || '');
            trackerUrl.searchParams.set('from_aninag', '1');

            window.location.href = trackerUrl.toString();
        }
    </script>
</body>
</html>
"""
