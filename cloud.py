"""Cloud run for the GitHub workflow (.github/workflows/refresh.yml).

Depending on what triggered the run, this refreshes the saved searches (daily schedule or the "Run
workflow" button), runs a one-off search typed into the "Run workflow" form, and runs searches that
anyone requests by opening an issue titled "Search: keyword, keyword". Results are merged with the
ones stored on the appgrab-state branch, so a push only rebuilds the page and a requested search
only searches its own keywords.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import appgrab
import filters
import report

STATE = Path("state")  # restored from and saved to the appgrab-state branch by the workflow
RESULTS_FILE = STATE / "results.json"
SEEN_FILE = STATE / "seen.json"
SITE_PAGE = Path("site/index.html")
MAX_REQUESTS_PER_RUN = 3  # the rest wait for the next run
MAX_KEYWORDS = 5
KEEP_DAYS = 14
KEYWORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 +#&/.\-]{0,59}$")
REQUEST_RE = re.compile(r"^\s*search\s*:\s*(.*)$", re.I | re.S)
ANYWHERE_TAG = "(also in descriptions)"


def github(method, path, body=None):
    """Call the GitHub REST API with the workflow's token."""
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json", "User-Agent": "appgrab"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def parse_request(title):
    """(keywords, anywhere) from a title like "Search: UVM, PCIe (also in descriptions)". Raises
    ValueError with a reason the requester can act on."""
    m = REQUEST_RE.match(title)
    if not m:
        raise ValueError('the title must start with "Search:"')
    text = m.group(1).strip()
    anywhere = text.lower().endswith(ANYWHERE_TAG)
    if anywhere:
        text = text[: -len(ANYWHERE_TAG)].strip()
    keywords = [k.strip() for k in text.split(",") if k.strip()]
    if not keywords:
        raise ValueError('there are no keywords after "Search:"')
    if len(keywords) > MAX_KEYWORDS:
        raise ValueError(f"use at most {MAX_KEYWORDS} keywords, separated by commas")
    if not all(KEYWORD_RE.match(k) for k in keywords):
        raise ValueError("keywords can only use letters, numbers, spaces and + # & / . - "
                         "and each must be 60 characters or fewer")
    return keywords, anywhere


def custom_search(keywords, anywhere, saved):
    search = filters.Search({"keywords": keywords, "match_description": anywhere}, adhoc=True)
    if any(s.slug == search.slug for s in saved):
        search.slug += "-custom"
    return search


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


def open_requests(repo):
    """Open issues asking for a search, oldest first."""
    try:
        issues = github("GET", f"/repos/{repo}/issues?state=open&sort=created&direction=asc&per_page=50") or []
    except urllib.error.URLError as e:
        print(f"Couldn't list search requests ({e}); skipping them this run.")
        return []
    return [i for i in issues if "pull_request" not in i and REQUEST_RE.match(i["title"])]


def reply(repo, issue, text):
    """Comment on a request and close it."""
    github("POST", f"/repos/{repo}/issues/{issue['number']}/comments", {"body": text})
    github("PATCH", f"/repos/{repo}/issues/{issue['number']}", {"state": "closed"})


def done_message(search, results, repo):
    rs = [r for r in results if r["search"] == search.slug]
    roles = [j for r in rs for j in r["jobs"]]
    junior = sum(j["level"] in ("new-grad", "entry") for j in roles)
    link = f"{pages_url(repo)}?search={urllib.parse.quote(search.slug)}"
    return (f"Done: found {len(roles)} matching roles ({junior} new grad or entry level) at "
            f"{sum(r['status'] == 'ok' for r in rs)} companies.\n\n"
            f"They'll be on the page in about a minute: {link}\n\n"
            f"This search stays in the page's Search dropdown for {KEEP_DAYS} days.")


def plan(event, saved, stored, repo):
    """Which searches to run, plus the requests (issues) each one answers."""
    have = {s["slug"] for s in (stored or {}).get("searches", [])}
    keywords = [k.strip() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
    searches, answers = {}, []
    if event == "schedule" or (event == "workflow_dispatch" and not keywords):
        searches.update((s.slug, s) for s in saved)
    else:  # pushes and requests only fill in saved searches that have no results yet
        searches.update((s.slug, s) for s in saved if s.slug not in have)
    if event == "workflow_dispatch" and keywords:
        try:
            kws, _ = parse_request("Search: " + ", ".join(keywords))
            s = custom_search(kws, os.environ.get("ANYWHERE") == "true", saved)
            searches.setdefault(s.slug, s)
        except ValueError as e:
            print(f"Skipping the one-off search: {e}")
    if os.environ.get("GITHUB_TOKEN") and repo:
        taken = 0
        for issue in open_requests(repo):
            try:
                kws, anywhere = parse_request(issue["title"])
            except ValueError as e:
                reply(repo, issue, f"Sorry, this search couldn't run: {e}. "
                                   "Open a new issue with a corrected title to try again.")
                continue
            if taken == MAX_REQUESTS_PER_RUN:
                continue  # stays open for the next run
            taken += 1
            s = custom_search(kws, anywhere, saved)
            s = searches.setdefault(s.slug, s)  # two requests for the same keywords share one search
            answers.append((s, issue))
    return list(searches.values()), answers


def main():
    sys.stdout.reconfigure(errors="replace")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    config, settings, saved = appgrab.load(appgrab.HERE / "companies.json")
    stored = stored_results(repo)
    searches, answers = plan(event, saved, stored, repo)
    now = dt.datetime.now(dt.timezone.utc)
    results = []
    if searches:
        print(f"Searching {len(config.get('companies', []))} companies for: {'; '.join(s.name for s in searches)}")
        results = appgrab.run(searches, config.get("companies", []), settings, appgrab.parse_args(["--no-open"]))
        appgrab.mark_new(results, now.isoformat(timespec="seconds"), SEEN_FILE)
        appgrab.summarize(searches, results)
    else:
        print("Nothing to search; rebuilding the page from stored results.")
    new = report.build_payload(results, searches, settings, now, repo)
    payload = report.merge(stored, new, [s.slug for s in saved], keep_days=KEEP_DAYS)
    report.render(payload, SITE_PAGE)
    STATE.mkdir(exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"Page has {len(payload['searches'])} searches and {len(payload['jobs'])} roles.")
    for search, issue in answers:
        reply(repo, issue, done_message(search, results, repo))


if __name__ == "__main__":
    main()
