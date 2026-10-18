"""Render scraped roles into the self-contained results page (template.html + embedded JSON)."""
import html
import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")
MAX_DESCRIPTION = 6000


def _clip(text):
    return text if len(text) <= MAX_DESCRIPTION else text[:MAX_DESCRIPTION].rstrip() + "\n…"


def render(results, settings, generated, out_path):
    jobs, companies = [], []
    for r in results:
        companies.append({"name": r["name"], "category": r["category"], "platform": r["platform"],
                          "status": r["status"], "message": r["message"], "count": len(r["jobs"]),
                          "checked": r["checked"], "link": r["link"]})
        for j in r["jobs"]:
            jobs.append({"key": j["key"], "company": r["name"], "category": r["category"],
                         "title": j["title"], "url": j["url"], "apply_url": j["apply_url"],
                         "location": j["location"], "posted": j["posted"], "pay": j["pay"],
                         "pay_max": j["pay_max"], "level": j["level"], "years": j["years"],
                         "requirements": j["requirements"], "description": _clip(j["description"]),
                         "workplace": j["workplace"], "new": j.get("new", False)})
    payload = {"generated": generated.isoformat(timespec="seconds"), "role": settings.role,
               "max_years": settings.max_years, "jobs": jobs, "companies": companies}
    # "<" never appears in JSON syntax, so escaping it inside strings keeps "</script>" from ending the block.
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    page = (TEMPLATE.read_text(encoding="utf-8")
            .replace("__APPGRAB_TITLE__", html.escape(settings.role or "Jobs"))
            .replace("__APPGRAB_DATA__", data))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
