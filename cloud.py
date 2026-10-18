"""Cloud run for the GitHub workflow (.github/workflows/refresh.yml). The shared page only shows the
saved searches in companies.json.

The daily schedule and the "Run workflow" button refresh every saved search. A push only rebuilds the
page from the results stored on the appgrab-state branch, searching just the saved searches that have
no results yet (for example one you just added to companies.json).
"""
import collections
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

import appgrab
import filters
import report

STATE = Path("state")  # restored from and saved to the appgrab-state branch by the workflow
RESULTS_FILE = STATE / "results.json"
SEEN_FILE = STATE / "seen.json"
SITE_PAGE = Path("site/index.html")


def pages_url(repo):
    owner, name = repo.split("/")
    return f"https://{owner.lower()}.github.io/{name}/"


def stored_results(repo):
    """Page data from earlier runs: the stored copy, or else the copy embedded in the live page."""
    if RESULTS_FILE.exists():
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    try:
        req = urllib.request.Request(pages_url(repo), headers={"User-Agent": "appgrab"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return report.page_data(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        print(f"No stored results, and the live page couldn't be read ({e}).")
        return None


def refresh_order(saved, day):
    """The first saved search always runs first and the others take turns running next, so a site
    that starts refusing requests partway through a run doesn't cut off the same searches every day."""
    rest = list(saved[1:])
    if len(rest) < 2:
        return list(saved)
    k = day % len(rest)
    return list(saved[:1]) + rest[k:] + rest[:k]


def refilter(payload, settings, saved):
    """Drop stored roles whose titles the saved searches no longer match (e.g. after adding
    exclude_title_terms), so such edits apply without searching the job sites again."""
    specs = {s.slug: s for s in saved}
    jobs = [j for j in payload["jobs"]
            if j["search"] not in specs or filters.title_class(j["title"], settings, specs[j["search"]]) != "reject"]
    counts = collections.Counter((j["search"], j["company"]) for j in jobs)
    companies = [{**c, "count": counts[(c["search"], c["name"])]} if c["status"] == "ok" else c
                 for c in payload["companies"]]
    return {**payload, "jobs": jobs, "companies": companies}


def main():
    sys.stdout.reconfigure(errors="replace")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    config, settings, saved = appgrab.load(appgrab.HERE / "companies.json")
    stored = stored_results(os.environ.get("GITHUB_REPOSITORY", ""))
    have = {s["slug"] for s in (stored or {}).get("searches", [])}
    refresh = event in ("schedule", "workflow_dispatch")
    now = dt.datetime.now(dt.timezone.utc)
    searches = refresh_order(saved, now.toordinal()) if refresh else [s for s in saved if s.slug not in have]
    results = []
    if searches:
        companies = config.get("companies", [])
        print(f"Searching {len(companies)} companies for: {'; '.join(s.name for s in searches)}")
        results = appgrab.run(searches, companies, settings, appgrab.parse_args(["--no-open"]))
        appgrab.mark_new(results, now.isoformat(timespec="seconds"), SEEN_FILE)
        appgrab.summarize(searches, results)
    else:
        print("Nothing to search; rebuilding the page from stored results.")
    new = report.build_payload(results, searches, settings, now)
    # max_custom=0: only saved searches appear on the shared page.
    payload = refilter(report.merge(stored, new, [s.slug for s in saved], max_custom=0), settings, saved)
    report.render(payload, SITE_PAGE)
    STATE.mkdir(exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"Page has {len(payload['searches'])} saved searches and {len(payload['jobs'])} roles.")


if __name__ == "__main__":
    main()
