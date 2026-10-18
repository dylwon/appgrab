"""Applicant-tracking systems with public JSON job APIs: Greenhouse, Lever, Ashby, SmartRecruiters."""
import html
import urllib.parse

from .common import format_pay, get_json, iso_date, join_unique, make_job

COUNTRIES = {"US": "United States", "CA": "Canada", "GB": "United Kingdom", "UK": "United Kingdom",
             "IN": "India", "IL": "Israel", "DE": "Germany", "FR": "France", "TW": "Taiwan",
             "CN": "China", "JP": "Japan", "KR": "South Korea", "SG": "Singapore", "NL": "Netherlands",
             "IE": "Ireland", "PL": "Poland", "CH": "Switzerland", "SE": "Sweden", "MX": "Mexico"}
WORKPLACE = {"onsite": "On-site", "on-site": "On-site", "hybrid": "Hybrid", "remote": "Remote"}


def _segments(url):
    return [s for s in urllib.parse.urlparse(url).path.split("/") if s]


def _workplace(value):
    return WORKPLACE.get(str(value or "").replace("_", "").lower(), "")


def greenhouse(company, ctx):
    url = company["careers_url"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    segs = _segments(url)
    token = (query.get("for") or [segs[segs.index("boards") + 1] if "boards" in segs else segs[0]])[0]
    api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    listing = get_json(api).get("jobs", [])
    hits = [j for j in listing
            if ctx.prefilter(j.get("title", ""), (j.get("location") or {}).get("name", ""))]

    def detail(job):
        d = get_json(f"{api}/{job['id']}?pay_transparency=true")
        pay = ""
        for r in d.get("pay_input_ranges") or []:
            lo, hi = (r.get("min_cents") or 0) / 100, (r.get("max_cents") or 0) / 100
            pay = format_pay(lo, hi, hourly=0 < hi < 500, currency=r.get("currency_type") or "USD")[0]
            if pay:
                break
        location = (d.get("location") or {}).get("name") or join_unique(
            o.get("location") or o.get("name") for o in d.get("offices") or [])
        link = d.get("absolute_url") or job.get("absolute_url")
        apply = link + "#app" if "greenhouse.io" in urllib.parse.urlparse(link).netloc else link
        return make_job(d["id"], d.get("title") or job.get("title"), link, apply, location,
                        iso_date(d.get("first_published") or d.get("updated_at")),
                        html.unescape(d.get("content") or ""), pay=pay)

    return [j for j in ctx.pmap(detail, hits[: ctx.max_details]) if j]


def lever(company, ctx):
    url = company["careers_url"]
    api_host = "api.eu.lever.co" if ".eu." in urllib.parse.urlparse(url).netloc else "api.lever.co"
    postings = get_json(f"https://{api_host}/v0/postings/{_segments(url)[0]}?mode=json")
    jobs = []
    for d in postings:
        cats = d.get("categories") or {}
        country = COUNTRIES.get(d.get("country") or "", d.get("country") or "")
        locations = cats.get("allLocations") or [cats.get("location")]
        location = join_unique(f"{loc}, {country}" if country and country not in loc else loc
                               for loc in locations if loc)
        if not ctx.prefilter(d.get("text", ""), location):
            continue
        body = [d.get("description") or ""]
        for section in d.get("lists") or []:
            body.append(f"<h3>{html.escape(section.get('text') or '')}</h3><ul>{section.get('content') or ''}</ul>")
        body.append(d.get("additional") or "")
        salary = d.get("salaryRange") or {}
        pay = format_pay(salary.get("min"), salary.get("max"), hourly="hour" in (salary.get("interval") or ""),
                         currency=salary.get("currency") or "USD")[0] if salary else ""
        jobs.append(make_job(d["id"], d.get("text", ""), d.get("hostedUrl"), d.get("applyUrl"), location,
                             iso_date(d.get("createdAt")), "".join(body), pay=pay,
                             employment_type=cats.get("commitment", ""),
                             workplace=_workplace(d.get("workplaceType"))))
    return jobs


def _ashby_location(name, address):
    postal = (address or {}).get("postalAddress") or {}
    parts = [postal.get("addressLocality"), postal.get("addressRegion"), postal.get("addressCountry")]
    return ", ".join(p for p in parts if p) or name or ""


def ashby(company, ctx):
    org = _segments(company["careers_url"])[0]
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
    jobs = []
    for d in data.get("jobs", []):
        if d.get("isListed") is False:
            continue
        location = join_unique([_ashby_location(d.get("location"), d.get("address"))] + [
            _ashby_location(s.get("location"), s.get("address")) for s in d.get("secondaryLocations") or []])
        if not ctx.prefilter(d.get("title", ""), location):
            continue
        comp = d.get("compensation") or {}
        pay = ""
        for c in comp.get("summaryComponents") or []:
            if c.get("compensationType") == "Salary" and (c.get("minValue") or c.get("maxValue")):
                pay = format_pay(c.get("minValue"), c.get("maxValue"),
                                 hourly="HOUR" in (c.get("interval") or "").upper(),
                                 currency=c.get("currencyCode") or "USD")[0]
                break
        workplace = "Remote" if d.get("isRemote") else _workplace(d.get("workplaceType"))
        jobs.append(make_job(d["id"], d.get("title", ""), d.get("jobUrl"), d.get("applyUrl"), location,
                             iso_date(d.get("publishedAt")), d.get("descriptionHtml") or "",
                             description=d.get("descriptionPlain") or "",
                             pay=pay or comp.get("scrapeableCompensationSalarySummary") or "",
                             employment_type=(d.get("employmentType") or "").replace("FullTime", "Full time"),
                             workplace=workplace))
    return jobs


def _sr_location(loc):
    loc = loc or {}
    country = COUNTRIES.get((loc.get("country") or "").upper(), loc.get("country") or "")
    return loc.get("fullLocation") or join_unique([loc.get("city"), loc.get("region"), country], ", ")


def smartrecruiters(company, ctx):
    cid = _segments(company["careers_url"])[0]
    api = f"https://api.smartrecruiters.com/v1/companies/{cid}/postings"
    found = {}
    for kw in ctx.keywords:
        offset = 0
        while offset < ctx.max_per_query:
            page = get_json(f"{api}?q={urllib.parse.quote(kw)}&limit={min(100, ctx.max_per_query)}&offset={offset}")
            items = page.get("content") or []
            for item in items:
                found.setdefault(item["id"], item)
            offset += len(items)
            if not items or offset >= (page.get("totalFound") or 0):
                break
    hits = [i for i in found.values() if ctx.prefilter(i.get("name", ""), _sr_location(i.get("location")))]

    def detail(item):
        d = get_json(item.get("ref") or f"{api}/{item['id']}")
        sections = (d.get("jobAd") or {}).get("sections") or {}
        body = "".join(f"<h3>{html.escape(s.get('title') or '')}</h3>{s.get('text')}"
                       for key in ("jobDescription", "qualifications", "additionalInformation")
                       for s in [sections.get(key) or {}] if s.get("text"))
        loc = d.get("location") or {}
        workplace = "Remote" if loc.get("remote") else "Hybrid" if loc.get("hybrid") else ""
        link = d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{cid}/{d['id']}"
        return make_job(d["id"], d.get("name", ""), link, d.get("applyUrl"), _sr_location(loc),
                        iso_date(d.get("releasedDate")), body,
                        employment_type=(d.get("typeOfEmployment") or {}).get("label", ""), workplace=workplace)

    return [j for j in ctx.pmap(detail, hits[: ctx.max_details]) if j]
