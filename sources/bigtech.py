"""Adapters for large company career sites with custom search pages."""
import html
import json
import re
import urllib.parse

from .common import get_json, get_text, html_lines, iso_date, join_unique, lines_to_text, make_job


_STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY", "District of Columbia": "DC",
}


def _pick(obj, *names):
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else None
        if value not in (None, "", [], {}):
            return value
    return ""


def _strip_tags(markup):
    if not markup:
        return ""
    return lines_to_text(html_lines(markup))


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "job"


def _location_text(*values):
    parts = []
    for value in values:
        if isinstance(value, list):
            for item in value:
                parts.append(_one_location(item))
        else:
            parts.append(_one_location(value))
    return join_unique(parts)


def _one_location(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    city = value.get("city") or value.get("name")
    state = value.get("state") or value.get("stateProvince")
    country = value.get("country") or value.get("countryName")
    if state in _STATE_ABBR:
        state = _STATE_ABBR[state]
    return join_unique([city, state, country], ", ")


def _amazon_location(hit):
    """amazon.jobs repeats each place in several formats; the normalized one is enough."""
    return _location_text(hit.get("normalized_location") or hit.get("location")) or join_unique(
        [hit.get("city"), hit.get("state"), hit.get("country_code")], ", ")


def amazon(company, ctx):
    """Fetch jobs from amazon.jobs search JSON; descriptions are included in search results."""
    seen = {}
    for keyword in ctx.keywords:
        offset = 0
        while offset < ctx.max_per_query:
            limit = min(100, ctx.max_per_query - offset)
            q = urllib.parse.urlencode({"base_query": keyword, "result_limit": limit, "offset": offset})
            payload = get_json(f"https://www.amazon.jobs/en/search.json?{q}")
            jobs = payload.get("jobs") if isinstance(payload, dict) else []
            total = int(payload.get("hits") or len(jobs))
            if not jobs:
                break
            for hit in jobs:
                job_id = _pick(hit, "id_icims", "id", "job_id")
                if job_id and str(job_id) not in seen and ctx.prefilter(hit.get("title", ""), _amazon_location(hit)):
                    seen[str(job_id)] = hit
            offset += len(jobs)
            if offset >= total or len(jobs) < limit:
                break

    out = []
    for hit in list(seen.values())[:ctx.max_details]:
        job_path = hit.get("job_path") or hit.get("jobPath") or ""
        url = urllib.parse.urljoin("https://www.amazon.jobs", job_path)
        requirements = []
        if hit.get("basic_qualifications"):
            requirements = [text for _, text in html_lines(hit.get("basic_qualifications")) if text][:12]
        desc_parts = [hit.get("description") or "", hit.get("basic_qualifications") or "",
                      hit.get("preferred_qualifications") or ""]
        description_html = "\n".join(part for part in desc_parts if part)
        out.append(make_job(
            id=_pick(hit, "id_icims", "id", "job_id"),
            title=hit.get("title", ""),
            url=url,
            apply_url=hit.get("url_next_step") or url,
            location=_amazon_location(hit),
            posted=iso_date(hit.get("posted_date")),
            description_html=description_html,
            requirements=requirements,
            employment_type=hit.get("job_type") or "",
            workplace=hit.get("normalized_work_location") or "",
        ))
    return out


def _hydration_json(text):
    m = re.search(r'__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:\\.|[^"\\])*)"\)', text)
    if not m:
        return {}
    # The blob is a JavaScript string literal: decode its escapes as JSON so non-ASCII text survives.
    raw = re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", m.group(1).replace("\\'", "'"))
    return json.loads(json.loads(f'"{raw}"'))


def _apple_search(keyword, page):
    q = urllib.parse.urlencode({
        "search": keyword,
        "sort": "relevance",
        "location": "united-states-USA",
        "page": page,
    })
    data = _hydration_json(get_text(f"https://jobs.apple.com/en-us/search?{q}"))
    search = (data.get("loaderData") or {}).get("search") or {}
    return search.get("searchResults") or [], int(search.get("totalRecords") or 0)


def _apple_detail(hit):
    job_id = hit.get("positionId") or hit.get("id")
    title = hit.get("postingTitle", "")
    slug = hit.get("transformedPostingTitle") or _slug(title)
    url = f"https://jobs.apple.com/en-us/details/{job_id}/{slug}"
    data = _hydration_json(get_text(url))
    detail = ((data.get("loaderData") or {}).get("jobDetails") or {}).get("jobsData") or {}
    if not detail:
        detail = hit
    # Apple puts the base-pay sentence in a "Pay & Benefits" footer, separate from the description.
    footers = [loc.get("content") or "" for footer in detail.get("postingFooters") or []
               for loc in (footer.get("localizations") or {}).get("en_US") or []]
    pay_footer = next((f for f in footers if "$" in f), "")
    desc_parts = [
        detail.get("jobSummary") or "",
        detail.get("description") or "",
        detail.get("minimumQualifications") or "",
        detail.get("preferredQualifications") or "",
        pay_footer,
    ]
    minq = detail.get("minimumQualifications") or ""
    prefq = detail.get("preferredQualifications") or ""
    requirements = [text for _, text in html_lines("\n".join([minq, prefq])) if text][:12]
    return make_job(
        id=job_id,
        title=detail.get("postingTitle") or title,
        url=url,
        apply_url=url,
        location=_location_text(detail.get("locations") or hit.get("locations")),
        posted=iso_date(detail.get("postDateInGMT") or detail.get("postingDate") or hit.get("postDateInGMT")),
        description="\n\n".join(part for part in desc_parts if part),
        requirements=requirements,
        employment_type=detail.get("employmentType") or detail.get("jobType") or "",
        workplace="Remote" if detail.get("homeOffice") else "",
    )


def apple(company, ctx):
    """Fetch jobs from Apple search/detail hydration JSON."""
    seen = {}
    for keyword in ctx.keywords:
        page = 1
        fetched = 0
        while fetched < ctx.max_per_query:
            results, total = _apple_search(keyword, page)
            if not results:
                break
            for hit in results:
                job_id = hit.get("positionId") or hit.get("id")
                loc = _location_text(hit.get("locations"))
                if job_id and str(job_id) not in seen and ctx.prefilter(hit.get("postingTitle", ""), loc):
                    seen[str(job_id)] = hit
            fetched += len(results)
            if fetched >= total or fetched >= ctx.max_per_query:
                break
            page += 1
    return [job for job in ctx.pmap(_apple_detail, list(seen.values())[:ctx.max_details]) if job]


def _html_text(fragment):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _google_search(keyword, page):
    q = urllib.parse.urlencode({"q": keyword, "page": page})
    text = get_text(f"https://www.google.com/about/careers/applications/jobs/results?{q}")
    jobs = []
    seen = set()
    pattern = re.compile(
        r'href="(?P<href>jobs/results/(?P<id>\d+)-[^"#?]+)[^"]*".*?'
        r'<h3[^>]*>(?P<title>.*?)</h3>.*?'
        r'<span class="r0wTof\s*">(.*?)</span>',
        re.S,
    )
    for m in pattern.finditer(text):
        job_id = m.group("id")
        if job_id in seen:
            continue
        seen.add(job_id)
        jobs.append({
            "id": job_id,
            "href": html.unescape(m.group("href")),
            "title": _html_text(m.group("title")),
            "location": _html_text(m.group(4)),
        })
    if not jobs:
        for m in re.finditer(r'href="(?P<href>jobs/results/(?P<id>\d+)-[^"#?]+)[^"]*"', text):
            job_id = m.group("id")
            if job_id not in seen:
                seen.add(job_id)
                jobs.append({"id": job_id, "href": html.unescape(m.group("href")), "title": "", "location": ""})
    return jobs


def _section_from(text, heading, stop_headings=()):
    m = re.search(rf"<h3>{re.escape(heading)}:?</h3>(?P<body>.*?)(?=<h3>|</div><div|$)", text, re.S)
    if not m:
        return ""
    body = m.group("body")
    if stop_headings:
        stop = re.search("|".join(re.escape(h) for h in stop_headings), body)
        if stop:
            body = body[:stop.start()]
    return body


def _google_detail(hit):
    url = urllib.parse.urljoin("https://www.google.com/about/careers/applications/", hit.get("href") or "")
    text = get_text(url)
    title = hit.get("title") or html.unescape(_pick(
        {"title": (re.search(r'<meta property="og:title" content="([^"]+)"', text) or ["", ""])[1]},
        "title",
    ))
    title = re.sub(r"\s+—\s+Google Careers$", "", title)
    mloc = re.search(r'<h2 class="p1N2lc">.*?</h2>.*?<span class="r0wTof\s*">(.*?)</span>', text, re.S)
    location = hit.get("location") or (_html_text(mloc.group(1)) if mloc else "")
    req_html = "\n".join([
        _section_from(text, "Minimum qualifications"),
        _section_from(text, "Preferred qualifications"),
    ])
    about_html = _section_from(text, "About the job")
    resp_html = _section_from(text, "Responsibilities")
    description = lines_to_text(html_lines("\n".join(part for part in [about_html, resp_html, req_html] if part)))
    salary = re.search(r"US:\s*(\$\s?[\d,]+\s*-\s*\$\s?[\d,]+)", text)
    if salary:
        description += f"\n\nUS base salary range: {salary.group(1)}"
    requirements = [line for _, line in html_lines(req_html) if line][:12]
    return make_job(
        id=hit.get("id") or (re.search(r"/results/(\d+)-", url) or ["", ""])[1],
        title=title,
        url=url,
        apply_url=url,
        location=location,
        posted="",
        description=description,
        requirements=requirements,
    )


def google(company, ctx):
    """Fetch Google jobs from server-rendered result/detail HTML.

    Google does not expose a stable posted-date field in this HTML, so posted is best-effort blank.
    """
    seen = {}
    for keyword in ctx.keywords:
        page = 1
        fetched = 0
        while fetched < ctx.max_per_query:
            jobs = _google_search(keyword, page)
            if not jobs:
                break
            for hit in jobs:
                if hit["id"] not in seen and ctx.prefilter(hit.get("title", ""), hit.get("location", "")):
                    seen[hit["id"]] = hit
            fetched += len(jobs)
            if len(jobs) < 20:
                break
            page += 1
    return [job for job in ctx.pmap(_google_detail, list(seen.values())[:ctx.max_details]) if job]
