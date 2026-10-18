"""Adapters for enterprise hiring platforms used by semiconductor companies."""
import html
import json
import re
import threading
import time
import urllib.parse

from .common import (FetchError, Unsupported, format_pay, get_json, get_text, html_lines, iso_date,
                     join_unique, make_job, post_json)


def _host(url):
    return urllib.parse.urlparse(url or "").netloc.lower()


def _root(url):
    p = urllib.parse.urlparse(url or "")
    return f"{p.scheme or 'https'}://{p.netloc}"


def _query_param(url, name):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query).get(name, [""])[0]


def _domain_from_careers(url):
    explicit = _query_param(url, "domain")
    if explicit:
        return explicit
    host = _host(url)
    if "microsoft" in host:
        return "microsoft.com"
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _pick(obj, *names):
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else None
        if value not in (None, "", [], {}):
            return value
    return ""


def _text(value):
    if isinstance(value, str):
        return " ".join(html.unescape(value).split())
    return ""


def _first_job_list(payload):
    if not isinstance(payload, dict):
        return [], 0
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    jobs = data.get("positions") or payload.get("positions") or []
    total = data.get("count") or data.get("totalHits") or payload.get("count") or len(jobs)
    return jobs, int(total or len(jobs))


def _location_from_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return join_unique([
            value.get("name") or value.get("city"),
            value.get("state") or value.get("region"),
            value.get("country") or value.get("countryName"),
        ], ", ")
    return ""


def _locations(*values):
    parts = []
    for value in values:
        if isinstance(value, list):
            parts.extend(_location_from_value(v) for v in value)
        else:
            parts.append(_location_from_value(value))
    return join_unique(parts)


def _public_url(root, job_id, hit, domain=""):
    url = _pick(hit, "canonicalPositionUrl", "canonicalUrl", "externalPath", "jobUrl", "url")
    if isinstance(url, str) and url:
        return urllib.parse.urljoin(root, url)
    pos = _pick(hit, "positionUrl", "positionPath")
    if isinstance(pos, str) and pos:
        return urllib.parse.urljoin(root, pos)
    return f"{root}/careers/job/{job_id}" + (f"?domain={urllib.parse.quote(domain)}" if domain else "")


def _eightfold_search(root, domain, keyword, start, num, prefer_pcsx=False):
    q = urllib.parse.urlencode({"domain": domain, "query": keyword, "start": start, "num": num})
    if not prefer_pcsx:
        url = f"{root}/api/apply/v2/jobs?{q}"
        try:
            return get_json(url)
        except FetchError as e:
            if e.status not in (403, 404):
                raise
    q = urllib.parse.urlencode({"domain": domain, "query": keyword, "location": "", "start": start})
    return get_json(f"{root}/api/pcsx/search?{q}")


def _eightfold_detail(root, domain, job_id, prefer_pcsx=False):
    if not prefer_pcsx:
        url = f"{root}/api/apply/v2/jobs/{urllib.parse.quote(str(job_id))}?domain={urllib.parse.quote(domain)}"
        try:
            return get_json(url)
        except FetchError as e:
            if e.status not in (403, 404):
                raise
    q = urllib.parse.urlencode({"position_id": str(job_id), "domain": domain, "hl": "en"})
    return get_json(f"{root}/api/pcsx/position_details?{q}")


def _eightfold_job(root, domain, hit, detail):
    data = detail.get("data") if isinstance(detail.get("data"), dict) else detail
    if not isinstance(data, dict):
        data = hit
    job_id = _pick(data, "id", "positionId", "displayJobId") or _pick(hit, "id", "positionId")
    title = _pick(data, "name", "title") or _pick(hit, "name", "title")
    url = _public_url(root, job_id, data or hit, domain)
    desc = _pick(data, "jobDescription", "job_description", "description", "descriptionHtml", "body")
    apply_url = _pick(data, "applyUrl", "apply_url", "applicationUrl") or url
    pay_text = ""
    lo = _pick(data, "salaryMin", "minSalary", "minimumSalary")
    hi = _pick(data, "salaryMax", "maxSalary", "maximumSalary")
    if lo or hi:
        try:
            pay_text = format_pay(float(lo or hi), float(hi or lo), False)[0]
        except (TypeError, ValueError):
            pay_text = ""
    return make_job(
        id=job_id,
        title=title,
        url=url,
        apply_url=apply_url,
        location=_locations(data.get("locations"), data.get("location"), hit.get("locations"), hit.get("location")),
        posted=iso_date(_pick(data, "postedDate", "postedTs", "dateCreated", "creationTs", "t_create", "createdAt") or
                        _pick(hit, "postedDate", "postedTs", "dateCreated", "creationTs", "t_create", "createdAt")),
        description_html=desc if isinstance(desc, str) and "<" in desc else "",
        description=desc if isinstance(desc, str) and "<" not in desc else "",
        pay=pay_text,
        employment_type=_text(_pick(data, "employmentType", "jobType", "type")),
        workplace=_text(_pick(data, "workplaceType", "workplace", "remoteType")),
    )


# Eightfold-hosted career sites share rate limits on Eightfold's side, so search them one at a time.
_EIGHTFOLD_GATE = threading.Semaphore(1)
_EIGHTFOLD_PAUSE = 0.4  # seconds between search pages


def eightfold(company, ctx):
    """Fetch jobs from Eightfold's public search APIs."""
    with _EIGHTFOLD_GATE:
        return _eightfold(company, ctx)


def _eightfold(company, ctx):
    root = _root(company.get("careers_url", ""))
    domain = _domain_from_careers(company.get("careers_url", ""))
    prefer_pcsx = "microsoft" in root
    seen, listed = {}, set()
    throttled = False
    for keyword in ctx.keywords:
        start = 0
        while start < ctx.max_per_query:
            num = min(10, ctx.max_per_query - start)
            if listed:
                time.sleep(_EIGHTFOLD_PAUSE)
            try:
                payload = _eightfold_search(root, domain, keyword, start, num, prefer_pcsx)
            except FetchError as e:
                # Throttled or refused mid-search: keep what we already have instead of failing.
                if e.status in (403, 429) and (start or seen):
                    throttled = True
                    break
                raise
            jobs, total = _first_job_list(payload)
            if not jobs:
                break
            fresh = 0
            for hit in jobs:
                if not isinstance(hit, dict):
                    continue
                job_id = str(_pick(hit, "id", "positionId", "displayJobId") or "")
                if not job_id or job_id in listed:
                    continue
                listed.add(job_id)
                fresh += 1
                if ctx.prefilter(_pick(hit, "name", "title"), _locations(hit.get("locations"), hit.get("location"))):
                    seen[job_id] = hit
            start += len(jobs)
            # Keywords overlap heavily; once a page brings nothing new, later pages won't either.
            if not fresh or start >= total or len(jobs) < num:
                break
        if throttled:
            ctx.log("rate-limited; using the results fetched so far")
            break
    hits = list(seen.values())[:ctx.max_details]

    def load(hit):
        job_id = _pick(hit, "id", "positionId", "displayJobId")
        detail = _eightfold_detail(root, domain, job_id, prefer_pcsx)
        data = detail.get("data") if isinstance(detail.get("data"), dict) else detail
        if not _pick(data, "jobDescription", "job_description", "description", "descriptionHtml", "body"):
            detail = _eightfold_detail(root, domain, job_id, True)
        return _eightfold_job(root, domain, hit, detail)

    return [job for job in ctx.pmap(load, hits) if job]


def _oracle_site(url):
    m = re.search(r"/sites/([^/]+)/", urllib.parse.urlparse(url or "").path)
    return m.group(1) if m else "CX"


def _oracle_detail(root, site, job_id):
    finder = f'ById;Id="{job_id}",siteNumber={site}'
    q = urllib.parse.urlencode({"expand": "all", "onlyData": "true", "finder": finder}, safe=';,="')
    payload = get_json(f"{root}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?{q}")
    items = payload.get("items") if isinstance(payload, dict) else None
    return items[0] if items else {}


def _oracle_location(job):
    locs = [job.get("PrimaryLocation")]
    locs.extend(job.get("secondaryLocations") or job.get("SecondaryLocations") or [])
    return _locations(locs)


def _oracle_requirements(job):
    quals = job.get("ExternalQualificationsStr") or ""
    return [_text(t) for kind, t in html_lines(quals) if kind != "h" and _text(t)][:8]


def oracle(company, ctx):
    """Fetch jobs from Oracle Recruiting Cloud candidate-experience APIs."""
    root = _root(company.get("careers_url", ""))
    site = _oracle_site(company.get("careers_url", ""))
    seen = {}
    for keyword in ctx.keywords:
        offset = 0
        while offset < ctx.max_per_query:
            limit = min(25, ctx.max_per_query - offset)
            finder = f'findReqs;siteNumber={site},limit={limit},offset={offset},keyword="{keyword}",sortBy=RELEVANCY'
            q = urllib.parse.urlencode({"onlyData": "true", "expand": "requisitionList", "finder": finder},
                                       safe=';,="')
            payload = get_json(f"{root}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?{q}")
            items = payload.get("items") if isinstance(payload, dict) else None
            bucket = items[0] if items else {}
            jobs = bucket.get("requisitionList") or []
            total = int(bucket.get("TotalJobsCount") or len(jobs))
            if not jobs:
                break
            for hit in jobs:
                job_id = hit.get("Id")
                loc = _oracle_location(hit)
                if job_id and str(job_id) not in seen and ctx.prefilter(hit.get("Title", ""), loc):
                    seen[str(job_id)] = hit
            offset += len(jobs)
            if offset >= total or len(jobs) < limit:
                break
    hits = list(seen.values())[:ctx.max_details]

    def load(hit):
        job_id = hit.get("Id")
        detail = _oracle_detail(root, site, job_id)
        job = {**hit, **detail}
        desc_parts = [
            job.get("ExternalDescriptionStr") or "",
            job.get("ExternalResponsibilitiesStr") or "",
            job.get("ExternalQualificationsStr") or "",
        ]
        desc_html = "\n".join(part for part in desc_parts if part)
        url = f"{root}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}"
        return make_job(
            id=job_id,
            title=job.get("Title", ""),
            url=url,
            apply_url=job.get("ApplyURL") or url,
            location=_oracle_location(job),
            posted=iso_date(job.get("PostedDate") or hit.get("PostedDate")),
            description_html=desc_html,
            requirements=_oracle_requirements(job),
            employment_type=_text(job.get("WorkerType") or job.get("JobSchedule") or job.get("JobType")),
            workplace=_text(job.get("WorkplaceType")),
        )

    return [job for job in ctx.pmap(load, hits) if job]


def jibe(company, ctx):
    """Fetch jobs from Jibe's public jobs API."""
    root = _root(company.get("careers_url", ""))
    seen = {}
    for keyword in ctx.keywords:
        page = 1
        fetched = 0
        while fetched < ctx.max_per_query:
            limit = min(10, ctx.max_per_query - fetched)
            q = urllib.parse.urlencode({"keywords": keyword, "page": page, "limit": limit})
            payload = get_json(f"{root}/api/jobs?{q}")
            jobs = payload.get("jobs") if isinstance(payload, dict) else []
            total = int(payload.get("totalCount") or len(jobs))
            if not jobs:
                break
            for item in jobs:
                hit = item.get("data") if isinstance(item, dict) and isinstance(item.get("data"), dict) else item
                if not isinstance(hit, dict):
                    continue
                job_id = _pick(hit, "req_id", "slug", "id")
                loc = _locations(hit.get("locations"), hit.get("location_name"),
                                 join_unique([hit.get("city"), hit.get("state"), hit.get("country")], ", "))
                if job_id and str(job_id) not in seen and ctx.prefilter(hit.get("title", ""), loc):
                    seen[str(job_id)] = hit
            fetched += len(jobs)
            if fetched >= total or len(jobs) < limit:
                break
            page += 1
    out = []
    for hit in list(seen.values())[:ctx.max_details]:
        job_id = _pick(hit, "req_id", "slug", "id")
        url = _pick(hit, "url", "job_url") or f"{root}/careers-home/jobs/{job_id}?lang=en-us"
        out.append(make_job(
            id=job_id,
            title=hit.get("title", ""),
            url=urllib.parse.urljoin(root, url),
            apply_url=hit.get("apply_url") or url,
            location=_locations(hit.get("locations"), hit.get("location_name"),
                                join_unique([hit.get("city"), hit.get("state"), hit.get("country")], ", ")),
            posted=iso_date(_pick(hit, "posted_date", "postedDate", "created_at", "updated_at")),
            description_html=hit.get("description", "") if "<" in str(hit.get("description", "")) else "",
            description=hit.get("description", "") if "<" not in str(hit.get("description", "")) else "",
            employment_type=_text(hit.get("employment_type")),
            workplace=_text(hit.get("workplace_type") or hit.get("remote_type")),
        ))
    return out


def _json_object_after(text, token):
    idx = text.find(token)
    if idx < 0:
        return {}
    start = text.find("{", idx)
    if start < 0:
        return {}
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    return {}


def _slug(title):
    text = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return text or "job"


def _phenom_base(url):
    """Site root plus locale prefix, e.g. https://careers.cisco.com/global/en."""
    p = urllib.parse.urlparse(url or "")
    prefix = p.path.split("/search-results")[0].rstrip("/")
    return f"{p.scheme or 'https'}://{p.netloc}{prefix}"


def _phenom_search(base, keyword, offset):
    q = urllib.parse.urlencode({"keywords": keyword, "from": offset})
    html_text = get_text(f"{base}/search-results?{q}")
    return _json_object_after(html_text, '"eagerLoadRefineSearch"')


def _phenom_detail(base, hit):
    job_id = _pick(hit, "jobId", "jobSeqNo", "id")
    title = _pick(hit, "title", "jobTitle")
    detail_url = f"{base}/job/{job_id}/{_slug(title)}"
    html_text = get_text(detail_url)
    ddo = _json_object_after(html_text, "phApp.ddo =")
    job = (((ddo.get("jobDetail") or {}).get("data") or {}).get("job") or {})
    if not job:
        job = hit
    url = urllib.parse.urljoin(detail_url, _pick(job, "jobUrl", "url") or detail_url)
    return make_job(
        id=job_id,
        title=_pick(job, "title", "jobTitle") or title,
        url=url,
        apply_url=urllib.parse.urljoin(detail_url, _pick(job, "applyUrl", "apply_url") or url),
        location=_locations(job.get("location"), job.get("locations"), hit.get("location")),
        posted=iso_date(_pick(job, "postedDate", "datePosted", "posted_date") or hit.get("postedDate")),
        description_html=_pick(job, "description", "jobDescription"),
        employment_type=_text(_pick(job, "type", "employmentType")),
        workplace=_text(_pick(job, "RemoteType", "remoteType", "workplaceType")),
    )


def phenom(company, ctx):
    """Fetch jobs from Phenom pages with embedded DDO JSON."""
    root = _phenom_base(company.get("careers_url", ""))
    seen = {}
    for keyword in ctx.keywords:
        offset = 0
        while offset < ctx.max_per_query:
            try:
                payload = _phenom_search(root, keyword, offset)
            except FetchError as e:
                if e.status == 429 and offset:
                    break
                raise
            data = payload.get("data") if isinstance(payload, dict) else {}
            jobs = data.get("jobs") or []
            total = int(payload.get("totalHits") or len(jobs))
            if not jobs:
                break
            for hit in jobs:
                job_id = _pick(hit, "jobId", "jobSeqNo", "id")
                loc = _locations(hit.get("location"), hit.get("locations"))
                if job_id and str(job_id) not in seen and ctx.prefilter(_pick(hit, "title", "jobTitle"), loc):
                    seen[str(job_id)] = hit
            offset += len(jobs)
            if offset >= total or len(jobs) < 10:
                break
    return [job for job in ctx.pmap(lambda hit: _phenom_detail(root, hit),
                                    list(seen.values())[:ctx.max_details]) if job]
