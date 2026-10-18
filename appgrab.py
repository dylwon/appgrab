"""AppGrab: search the job boards in companies.json and build one page of matching roles.

    python appgrab.py                               # run the saved searches, write jobs.html, open it
    python appgrab.py --search "physical design"    # one-off search for any keywords
    python appgrab.py --only nvidia amd             # just companies whose name contains these words
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import json
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import filters
import report
import sources
from sources.common import Context, FetchError, Unsupported

HERE = Path(__file__).resolve().parent
SEEN_FILE = HERE / ".appgrab_seen.json"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Search the job boards in companies.json and build a results page.")
    p.add_argument("--search", nargs="+", metavar="KEYWORD",
                   help='one-off search for these keywords instead of the saved searches, '
                        'e.g. --search "physical design" "place and route"')
    p.add_argument("--anywhere", action="store_true",
                   help="with --search: also match the keywords in job descriptions, not just titles (slower)")
    p.add_argument("--with-saved", action="store_true", help="with --search: run the saved searches too")
    p.add_argument("--config", type=Path, default=HERE / "companies.json", help="companies file (default: companies.json)")
    p.add_argument("--out", type=Path,
                   help="results page (default: jobs.html, or jobs-<keywords>.html for a one-off --search)")
    p.add_argument("--only", nargs="+", metavar="NAME", help="only companies whose name contains one of these words")
    p.add_argument("--workers", type=int, default=6, help="sites searched in parallel (default: 6)")
    p.add_argument("--max-per-query", type=int, default=60, help="search results read per keyword per site (default: 60)")
    p.add_argument("--max-details", type=int, default=60, help="job postings opened per company per search (default: 60)")
    p.add_argument("--no-open", action="store_true", help="don't open the results page in a browser")
    p.add_argument("--verbose", action="store_true", help="print per-posting debug messages")
    return p.parse_args(argv)


def search_link(company, search):
    """The company's own search page for this search's first keyword. search_url uses the word
    "verification" as a stand-in search term; without one, fall back to the careers page."""
    url = company.get("search_url") or ""
    if "verification" in url:
        return url.replace("verification", urllib.parse.quote(search.keywords[0]))
    return url or company.get("careers_url") or ""


def scrape(company, search, settings, args):
    """Run one company's adapter for one search and keep the matching roles. Never raises."""
    platform = company.get("platform") or sources.detect(company.get("careers_url"))
    result = {"search": search.slug, "name": company["name"], "category": company.get("category", ""),
              "platform": platform or "", "link": search_link(company, search), "jobs": [], "checked": 0}
    if not company.get("careers_url"):
        return {**result, "status": "manual", "message": company.get("notes") or "No careers URL in companies.json"}
    log = (lambda m: print(f"    [{company['name']}] {m}")) if args.verbose else None
    ctx = Context(search.keywords, filters.make_prefilter(settings, search), max_per_query=args.max_per_query,
                  max_details=args.max_details, log=log)
    try:
        raw = sources.get(platform)(company, ctx)
    except Unsupported as e:
        return {**result, "status": "manual", "message": str(e)}
    except FetchError as e:
        reason = (f"site temporarily refused requests (HTTP {e.status}) - try again later"
                  if e.status in (403, 429) else str(e))
        return {**result, "status": "error", "message": f"Failed this run: {reason}"}
    except Exception as e:  # noqa: BLE001 - one broken site shouldn't stop the run
        return {**result, "status": "error", "message": f"Failed this run: {e}"}
    jobs, ids = [], set()
    for job in raw:
        kept = filters.evaluate(job, settings, search)
        if kept and kept["id"] not in ids:
            ids.add(kept["id"])
            kept["uid"] = f"{company['name']}|{kept['id']}"  # same posting across searches
            kept["key"] = f"{search.slug}|{kept['uid']}"
            jobs.append(kept)
    return {**result, "status": "ok", "message": f"{len(raw)} postings checked", "jobs": jobs, "checked": len(raw)}


def status_line(r):
    if r["status"] == "ok":
        levels = collections.Counter(j["level"] for j in r["jobs"])
        junior = levels["new-grad"] + levels["entry"]
        return f"{r['name']} ({r['platform']}): {len(r['jobs'])} roles" + (
            f", {junior} new grad / <=1 yr" if junior else "")
    label = "check by hand" if r["status"] == "manual" else "ERROR"
    return f"{r['name']}: {label} - {r['message']}"


def mark_new(results, run_id):
    """Flag roles a search hasn't found on any earlier run. Nothing is 'new' the first time a search
    runs, since everything would be."""
    try:
        seen = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    seen = {k: v for k, v in seen.items() if k.count("|") >= 2}  # drop keys from before per-search history
    searched_before = {key.split("|", 1)[0] for key in seen}
    for r in results:
        for j in r["jobs"]:
            first_seen = seen.setdefault(j["key"], run_id)
            j["new"] = r["search"] in searched_before and first_seen == run_id
    SEEN_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")


def choose_searches(args, saved):
    if not args.search:
        return saved
    adhoc = filters.Search({"keywords": args.search, "match_description": args.anywhere}, adhoc=True)
    searches = list(saved) if args.with_saved else []
    if any(s.slug == adhoc.slug for s in searches):
        adhoc.slug += "-one-off"
    return searches + [adhoc]


def main(argv=None):
    sys.stdout.reconfigure(errors="replace")
    args = parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    settings, saved = filters.load(config)
    searches = choose_searches(args, saved)
    if not searches:
        sys.exit('No searches to run: add one under "searches" in companies.json, or use --search.')
    one_off_only = args.search and not args.with_saved
    out = args.out or HERE / (f"jobs-{searches[-1].slug}.html" if one_off_only else "jobs.html")
    companies = config.get("companies", [])
    if args.only:
        wanted = [w.lower() for w in args.only]
        companies = [c for c in companies if any(w in c["name"].lower() for w in wanted)]
        if not companies:
            sys.exit(f"No company names contain: {', '.join(args.only)}")
    tasks = [(s, c) for s in searches for c in companies]
    print(f"Searching {len(companies)} companies for: {'; '.join(s.name for s in searches)} ...")
    started, results = time.time(), []
    with cf.ThreadPoolExecutor(max(1, args.workers)) as ex:
        futures = {ex.submit(scrape, c, s, settings, args): s for s, c in tasks}
        try:
            for i, fut in enumerate(cf.as_completed(futures), 1):
                results.append(fut.result())
                label = f"[{futures[fut].name}] " if len(searches) > 1 else ""
                print(f"[{i:>3}/{len(tasks)}] {label}{status_line(results[-1])}", flush=True)
        except KeyboardInterrupt:
            print("\nStopping early - saving what was found so far (finishing sites already in progress).")
            ex.shutdown(cancel_futures=True)
    order = {c["name"]: i for i, c in enumerate(config.get("companies", []))}
    rank = {s.slug: i for i, s in enumerate(searches)}
    results.sort(key=lambda r: (rank[r["search"]], order.get(r["name"], len(order))))
    run = dt.datetime.now().astimezone()
    mark_new(results, run.isoformat(timespec="seconds"))
    report.render(results, searches, settings, run, out)
    print()
    for s in searches:
        rs = [r for r in results if r["search"] == s.slug]
        by_hand = sum(r["status"] != "ok" for r in rs)
        print(f"{s.name}: {sum(len(r['jobs']) for r in rs)} roles from {len(rs) - by_hand} companies; "
              f"{by_hand} sites to check by hand (listed at the bottom of the page).")
    print(f"Finished in {time.time() - started:.0f}s. Saved {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
