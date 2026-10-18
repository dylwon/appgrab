"""AppGrab: find ASIC design-verification roles on the job boards in companies.json.

    python appgrab.py                     # search every company, write jobs.html, open it
    python appgrab.py --only nvidia amd   # just companies whose name contains these words
    python appgrab.py --no-open           # don't open the browser afterwards
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import json
import sys
import time
import webbrowser
from pathlib import Path

import filters
import report
import sources
from sources.common import Context, FetchError, Unsupported

HERE = Path(__file__).resolve().parent
SEEN_FILE = HERE / ".appgrab_seen.json"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Find ASIC design-verification roles (new grad through senior).")
    p.add_argument("--config", type=Path, default=HERE / "companies.json", help="companies file (default: companies.json)")
    p.add_argument("--out", type=Path, default=HERE / "jobs.html", help="results page (default: jobs.html)")
    p.add_argument("--only", nargs="+", metavar="NAME", help="only companies whose name contains one of these words")
    p.add_argument("--workers", type=int, default=6, help="companies searched in parallel (default: 6)")
    p.add_argument("--max-per-query", type=int, default=60, help="search results read per keyword per site (default: 60)")
    p.add_argument("--max-details", type=int, default=60, help="job postings opened per company (default: 60)")
    p.add_argument("--no-open", action="store_true", help="don't open the results page in a browser")
    p.add_argument("--verbose", action="store_true", help="print per-posting debug messages")
    return p.parse_args(argv)


def scrape(company, settings, args):
    """Run one company's adapter and keep the wanted roles. Never raises."""
    platform = company.get("platform") or sources.detect(company.get("careers_url"))
    result = {"name": company["name"], "category": company.get("category", ""), "platform": platform or "",
              "link": company.get("search_url") or company.get("careers_url") or "", "jobs": [], "checked": 0}
    if not company.get("careers_url"):
        return {**result, "status": "manual", "message": company.get("notes") or "No careers URL in companies.json"}
    log = (lambda m: print(f"    [{company['name']}] {m}")) if args.verbose else None
    ctx = Context(settings.keywords, filters.make_prefilter(settings), max_per_query=args.max_per_query,
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
        kept = filters.evaluate(job, settings)
        if kept and kept["id"] not in ids:
            ids.add(kept["id"])
            kept["key"] = f"{company['name']}|{kept['id']}"
            jobs.append(kept)
    return {**result, "status": "ok", "message": f"{len(raw)} postings checked", "jobs": jobs, "checked": len(raw)}


def status_line(r):
    if r["status"] == "ok":
        levels = collections.Counter(j["level"] for j in r["jobs"])
        junior = levels["new-grad"] + levels["entry"]
        return f"{r['name']} ({r['platform']}): {len(r['jobs'])} DV roles" + (
            f", {junior} new grad / <=1 yr" if junior else "")
    label = "check by hand" if r["status"] == "manual" else "ERROR"
    return f"{r['name']}: {label} - {r['message']}"


def mark_new(results, run_id):
    """Flag roles that weren't seen on any earlier run (nothing is 'new' on the very first run)."""
    try:
        seen = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    first_run = not seen
    for r in results:
        for j in r["jobs"]:
            first_seen = seen.setdefault(j["key"], run_id)
            j["new"] = not first_run and first_seen == run_id
    SEEN_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")


def main(argv=None):
    sys.stdout.reconfigure(errors="replace")
    args = parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    settings = filters.Settings(config.get("search", {}))
    companies = config.get("companies", [])
    if args.only:
        wanted = [w.lower() for w in args.only]
        companies = [c for c in companies if any(w in c["name"].lower() for w in wanted)]
        if not companies:
            sys.exit(f"No company names contain: {', '.join(args.only)}")
    print(f"Searching {len(companies)} companies for {settings.role or ', '.join(settings.keywords)} ...")
    started, results = time.time(), []
    with cf.ThreadPoolExecutor(max(1, args.workers)) as ex:
        futures = [ex.submit(scrape, c, settings, args) for c in companies]
        try:
            for i, fut in enumerate(cf.as_completed(futures), 1):
                results.append(fut.result())
                print(f"[{i:>2}/{len(companies)}] {status_line(results[-1])}", flush=True)
        except KeyboardInterrupt:
            print("\nStopping early - saving what was found so far (finishing sites already in progress).")
            ex.shutdown(cancel_futures=True)
    order = {c["name"]: i for i, c in enumerate(config.get("companies", []))}
    results.sort(key=lambda r: order.get(r["name"], len(order)))
    run = dt.datetime.now().astimezone()
    mark_new(results, run.isoformat(timespec="seconds"))
    report.render(results, settings, run, args.out)
    roles = sum(len(r["jobs"]) for r in results)
    by_hand = sum(r["status"] != "ok" for r in results)
    print(f"\n{roles} roles from {len(results) - by_hand} companies in {time.time() - started:.0f}s; "
          f"{by_hand} sites to check by hand (listed at the bottom of the page).")
    print(f"Saved {args.out}")
    if not args.no_open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
