"""Results-page data: build it from a run, merge it with stored data, and render the page."""
import datetime as dt
import html
import json
import re
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")
MAX_DESCRIPTION = 6000
DATA_RE = re.compile(r'<script id="data" type="application/json">(.*?)</script>', re.S)


def _clip(text):
    return text if len(text) <= MAX_DESCRIPTION else text[:MAX_DESCRIPTION].rstrip() + "\n…"


def build_payload(results, searches, settings, generated):
    """Page data for the searches that were just run."""
    stamp = generated.isoformat(timespec="seconds")
    jobs, companies = [], []
    for r in results:
        companies.append({"search": r["search"], "name": r["name"], "category": r["category"],
                          "platform": r["platform"], "status": r["status"], "message": r["message"],
                          "count": len(r["jobs"]), "checked": r["checked"], "link": r["link"]})
        for j in r["jobs"]:
            jobs.append({"search": r["search"], "key": j["key"], "uid": j["uid"], "company": r["name"],
                         "category": r["category"], "title": j["title"], "url": j["url"],
                         "apply_url": j["apply_url"], "location": j["location"], "posted": j["posted"],
                         "pay": j["pay"], "pay_max": j["pay_max"], "level": j["level"], "years": j["years"],
                         "requirements": j["requirements"], "description": _clip(j["description"]),
                         "workplace": j["workplace"], "new": j.get("new", False)})
    return {"generated": stamp, "max_years": settings.max_years,
            "searches": [{"slug": s.slug, "name": s.name, "keywords": s.keywords,
                          "kind": "custom" if s.adhoc else "saved", "anywhere": s.match_description,
                          "generated": stamp} for s in searches],
            "jobs": jobs, "companies": companies}


def _when(search, payload):
    """When a search ran. Pages from before per-search times only record the page's time."""
    return dt.datetime.fromisoformat(search.get("generated") or payload["generated"])


def merge(stored, new, saved_slugs, keep_days=14, max_custom=20):
    """Stored page data plus a new run. Searches in the new run replace their stored versions, saved
    searches still in companies.json are kept, and custom searches are kept for keep_days (at most
    max_custom of them, newest first)."""
    if not stored:
        return new
    fresh = {s["slug"] for s in new["searches"]}
    cutoff = dt.datetime.fromisoformat(new["generated"]) - dt.timedelta(days=keep_days)
    searches = list(new["searches"])
    for s in stored.get("searches", []):
        kind = s.get("kind") or ("custom" if s.get("one_off") else "saved")
        when = _when(s, stored)
        if s["slug"] in fresh or (kind == "saved" and s["slug"] not in saved_slugs):
            continue
        if kind == "custom" and when < cutoff:
            continue
        searches.append({**s, "kind": kind, "generated": when.isoformat(timespec="seconds")})
    order = {slug: i for i, slug in enumerate(saved_slugs)}
    saved = sorted((s for s in searches if s["kind"] == "saved"), key=lambda s: order.get(s["slug"], len(order)))
    custom = sorted((s for s in searches if s["kind"] == "custom"),
                    key=lambda s: dt.datetime.fromisoformat(s["generated"]), reverse=True)
    searches = saved + custom[:max_custom]
    kept = {s["slug"] for s in searches} - fresh
    return {**new, "searches": searches,
            "jobs": [j for j in stored.get("jobs", []) if j["search"] in kept] + new["jobs"],
            "companies": [c for c in stored.get("companies", []) if c["search"] in kept] + new["companies"]}


def page_data(page_html):
    """The page data embedded in a rendered results page, or None."""
    m = DATA_RE.search(page_html)
    return json.loads(m.group(1)) if m else None


def render(payload, out_path):
    """Write the self-contained results page for this page data."""
    title = payload["searches"][0]["name"] if payload["searches"] else "Jobs"
    # "<" never appears in JSON syntax, so escaping it inside strings keeps "</script>" from ending the block.
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    page = (TEMPLATE.read_text(encoding="utf-8")
            .replace("__APPGRAB_TITLE__", html.escape(title))
            .replace("__APPGRAB_DATA__", data))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
