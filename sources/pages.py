"""Adapters for job boards that publish mostly-static pages rather than clean APIs."""
import datetime as dt
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from .common import (
    FetchError,
    Unsupported,
    format_pay,
    get_text,
    html_lines,
    iso_date,
    join_unique,
    lines_to_text,
    make_job,
)


_BLOCKED_STATUS = {400, 403}
_MONEY = re.compile(r"([A-Z]{3})?\s*\$?\s*([\d,]+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(?:[A-Z]{3})?\s*\$?\s*([\d,]+(?:\.\d+)?)", re.I)


def _abs(url, base):
    return urllib.parse.urljoin(base, html.unescape(url or ""))


def _clean(text):
    return " ".join(html.unescape(str(text or "")).split())


def _strip_tags(markup):
    text = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", markup or "")
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(?:p|div|li|h[1-6]|tr|section|article)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return _clean(text)


def _first(pattern, text, flags=re.I | re.S, default=""):
    m = re.search(pattern, text or "", flags)
    return _clean(m.group(1)) if m else default


def _inner(pattern, text, flags=re.I | re.S, default=""):
    m = re.search(pattern, text or "", flags)
    return html.unescape(m.group(1).strip()) if m else default


def _decode_json_after(text, marker):
    i = (text or "").find(marker)
    if i < 0:
        return None
    start = min([p for p in (text.find("[", i), text.find("{", i)) if p >= 0], default=-1)
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return None


def _next_data(text):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None


def _json_ld(text):
    out = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text or "", re.I | re.S):
        try:
            data = json.loads(html.unescape(raw.strip()))
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("@graph"), list):
                out.extend(x for x in item["@graph"] if isinstance(x, dict) and x.get("@type") == "JobPosting")
    return out


def _ld_location(value):
    values = value if isinstance(value, list) else [value]
    parts = []
    for loc in values:
        if isinstance(loc, str):
            parts.append(loc)
            continue
        if not isinstance(loc, dict):
            continue
        if loc.get("name"):
            parts.append(loc.get("name"))
        addr = loc.get("address") or {}
        if isinstance(addr, str):
            parts.append(addr)
        elif isinstance(addr, dict):
            parts.append(join_unique([
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry"),
            ], ", "))
    return join_unique(parts)


def _amount(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _pay_from_text(value):
    m = _MONEY.search(_clean(value))
    if not m:
        return ""
    lo, hi = _amount(m.group(2)), _amount(m.group(3))
    if lo is None or hi is None:
        return ""
    hourly = max(lo, hi) < 500
    return format_pay(min(lo, hi), max(lo, hi), hourly, (m.group(1) or "USD").upper())[0]


def _date(value):
    value = _strip_tags(value) if isinstance(value, str) and "<" in value else value
    parsed = iso_date(value)
    if parsed:
        return parsed
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(str(value or "").strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _pay_from_struct(value):
    if not value:
        return ""
    if isinstance(value, list):
        for item in value:
            pay = _pay_from_struct(item)
            if pay:
                return pay
        return ""
    if not isinstance(value, dict):
        return ""
    if "value" in value and isinstance(value["value"], dict):
        return _pay_from_struct(value["value"])
    lo = next((_amount(value.get(k)) for k in (
        "minValue", "min", "minimum", "minimumValue", "low", "from", "salaryMin", "minSalary", "lowerBound",
    ) if value.get(k) not in (None, "")), None)
    hi = next((_amount(value.get(k)) for k in (
        "maxValue", "max", "maximum", "maximumValue", "high", "to", "salaryMax", "maxSalary", "upperBound",
    ) if value.get(k) not in (None, "")), None)
    single = _amount(value.get("value")) if not isinstance(value.get("value"), dict) else None
    lo, hi = lo or single, hi or single
    if lo is None and hi is None:
        return ""
    unit = str(value.get("unitText") or value.get("unit") or "").lower()
    hourly = "hour" in unit or max(lo or 0, hi or 0) < 500
    return format_pay(lo, hi, hourly, value.get("currency") or "USD")[0]


def _fetch_text(url, blocked_reason=False, **kw):
    try:
        return get_text(url, **kw)
    except FetchError as e:
        if blocked_reason and e.status in _BLOCKED_STATUS:
            raise Unsupported(f"site blocks automated requests (HTTP {e.status})") from None
        raise


def _xml_items(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    channel = root.find("channel")
    return [] if channel is None else channel.findall("item")


def _tag(item, name):
    return item.findtext(name) or ""


def _fields_from_labels(text):
    fields = {}
    rx = re.compile(
        r'<div[^>]*class="[^"]*article__content__view__field__label[^"]*"[^>]*>(.*?)</div>\s*'
        r'<div[^>]*class="[^"]*article__content__view__field__value[^"]*"[^>]*>(.*?)(?=<div[^>]*class="[^"]*article__content__view__field(?:\s|")|</article>)',
        re.I | re.S,
    )
    for label, value in rx.findall(text or ""):
        fields[_strip_tags(label).lower()] = html.unescape(value.strip())
    return fields


def _section_value(text, title):
    m = re.search(
        r'<h[1-6][^>]*>\s*' + re.escape(title) + r'\s*</h[1-6]>.*?'
        r'<div[^>]*class="[^"]*article__content__view__field__value[^"]*"[^>]*>(.*?)(?:</article>|<div class="article__header|</section>)',
        text or "",
        re.I | re.S,
    )
    return html.unescape(m.group(1).strip()) if m else ""


def _job_id_from_url(url):
    path = urllib.parse.urlparse(url or "").path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else url


def _prefiltered(ctx, items):
    return [item for item in items if ctx.prefilter(item.get("title", ""), item.get("location", ""))]


def rippling(company: dict, ctx) -> list[dict]:
    path_parts = [p for p in urllib.parse.urlparse(company["careers_url"]).path.split("/") if p]
    slug = path_parts[0] if path_parts else ""
    found = {}
    if slug:
        try:
            api = _fetch_text(f"https://ats.rippling.com/api/v2/board/{slug}/jobs?pageSize={ctx.max_per_query}", blocked_reason=True)
            data = json.loads(api)
            for item in data.get("items") or []:
                locations = []
                for loc in item.get("locations") or []:
                    if isinstance(loc, dict):
                        locations.append(loc.get("name") or join_unique([loc.get("city"), loc.get("state"), loc.get("country")], ", "))
                found[item.get("id")] = {
                    "id": item.get("id"),
                    "title": item.get("name"),
                    "location": join_unique(locations),
                    "url": item.get("url") or f"https://ats.rippling.com/{slug}/jobs/{item.get('id')}",
                }
        except (FetchError, ValueError):
            found = {}
    text = _fetch_text(company["careers_url"], blocked_reason=True)
    for m in re.finditer(r'href="(https://ats\.rippling\.com/[^"]+/jobs/([0-9a-f-]{32,}))"', text, re.I):
        block_start = text.rfind('<div class="card_card', 0, m.start())
        block = text[block_start:m.start()] if block_start >= 0 else text[max(0, m.start() - 1500):m.start()]
        title = _first(r'open-jobs_title[^>]*>(.*?)</span>', block)
        location = _first(r'open-jobs_place[^>]*>(.*?)</span>', block)
        if title:
            found[m.group(2)] = {"id": m.group(2), "title": title, "location": location, "url": m.group(1)}
    if not found:
        raise Unsupported("job list loads with JavaScript")

    items = _prefiltered(ctx, list(found.values()))[:ctx.max_details]

    def detail(item):
        page = _fetch_text(item["url"], blocked_reason=True)
        data = _next_data(page) or {}
        job = (((data.get("props") or {}).get("pageProps") or {}).get("apiData") or {}).get("jobPost") or {}
        desc = job.get("description") or {}
        description_html = join_unique([desc.get("role"), desc.get("company")], "\n") if isinstance(desc, dict) else str(desc or "")
        locations = job.get("workLocations") or [item["location"]]
        return make_job(
            job.get("uuid") or item["id"],
            job.get("name") or item["title"],
            job.get("url") or item["url"],
            location=join_unique(locations),
            posted=iso_date(job.get("createdOn")),
            description_html=description_html,
            pay=_pay_from_struct(job.get("payRangeDetails")),
            employment_type=((job.get("employmentType") or {}).get("id") if isinstance(job.get("employmentType"), dict) else ""),
        )

    return [job for job in ctx.pmap(detail, items) if job]


def jobvite(company: dict, ctx) -> list[dict]:
    parts = [p for p in urllib.parse.urlparse(company["careers_url"]).path.split("/") if p]
    slug = parts[0] if parts else ""
    if not slug:
        raise Unsupported("missing Jobvite company slug")
    list_url = f"https://jobs.jobvite.com/{slug}/jobs?nl=0"
    text = _fetch_text(list_url, blocked_reason=True)
    found = {}
    for m in re.finditer(r'<li[^>]*class="[^"]*flex-row[^"]*"[^>]*>(.*?)</li>', text, re.I | re.S):
        block = m.group(1)
        link = _first(r'href="([^"]*/' + re.escape(slug) + r'/job/([^"]+))"', block)
        id_match = re.search(r'/' + re.escape(slug) + r'/job/([^"?/#]+)', block)
        title = _first(r'jv-job-list-name[^>]*>(.*?)</div>', block)
        location = _first(r'jv-job-list-location[^>]*>(.*?)</div>', block)
        if link and id_match and title:
            job_id = id_match.group(1)
            found[job_id] = {"id": job_id, "title": title, "location": location, "url": _abs(link, list_url)}
    for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', text, re.I | re.S):
        block = m.group(1)
        id_match = re.search(r'href="([^"]*/' + re.escape(slug) + r'/job/([^"?/#]+))"', block)
        title = _first(r'jv-job-list-name[^>]*>.*?<a[^>]*>(.*?)</a>', block)
        location = _first(r'jv-job-list-location[^>]*>(.*?)</td>', block)
        if id_match and title:
            found.setdefault(id_match.group(2), {"id": id_match.group(2), "title": title, "location": location, "url": _abs(id_match.group(1), list_url)})
    if not found:
        raise Unsupported("job list loads with JavaScript")

    items = _prefiltered(ctx, list(found.values())[:ctx.max_per_query])[:ctx.max_details]

    def detail(item):
        url = item["url"] + ("&nl=0" if "?" in item["url"] else "?nl=0")
        page = _fetch_text(url, blocked_reason=True)
        ld = (_json_ld(page) or [{}])[0]
        desc = ld.get("description") or _inner(r'<div[^>]+class="jv-job-detail-description"[^>]*>(.*?)(?:<div class="jv-job-detail-bottom-actions"|</main>)', page)
        title = ld.get("title") or item["title"]
        location = _ld_location(ld.get("jobLocation")) or item["location"] or _first(r'jv-job-detail-meta[^>]*>.*?Location:\s*</[^>]+>\s*([^<]+)', page)
        return make_job(
            item["id"],
            title,
            url,
            apply_url=f"https://jobs.jobvite.com/{slug}/job/{item['id']}/apply",
            location=location,
            posted=iso_date(ld.get("datePosted")),
            description_html=desc,
            pay=_pay_from_struct(ld.get("baseSalary")),
            employment_type=ld.get("employmentType") or "",
        )

    return [job for job in ctx.pmap(detail, items) if job]


def comeet(company: dict, ctx) -> list[dict]:
    text = _fetch_text(company["careers_url"], blocked_reason=True)
    data = _decode_json_after(text, "COMPANY_POSITIONS_DATA =")
    if not isinstance(data, list):
        raise Unsupported("positions JSON was not found")
    items = []
    for pos in data[:ctx.max_per_query]:
        loc = pos.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else ""
        if isinstance(loc, dict) and loc.get("country") and loc.get("country") not in location:
            location = join_unique([location, loc.get("country")], ", ")
        items.append({"raw": pos, "id": pos.get("uid"), "title": pos.get("name"), "location": location})
    items = _prefiltered(ctx, items)[:ctx.max_details]

    def detail(item):
        pos = item["raw"]
        details = sorted((pos.get("custom_fields") or {}).get("details") or [], key=lambda d: d.get("order") or 0)
        desc_parts, requirements = [], []
        for part in details:
            name, value = part.get("name", ""), part.get("value", "")
            if re.search(r"requirement|qualification", name, re.I):
                requirements.extend(t for k, t in html_lines(value) if k == "li")
            desc_parts.append(value)
        return make_job(
            pos.get("uid"),
            pos.get("name"),
            pos.get("url_active_page") or pos.get("url_comeet_hosted_page") or company["careers_url"],
            location=item["location"],
            posted=iso_date(pos.get("time_updated")),
            description_html="\n".join(desc_parts),
            requirements=requirements[:8],
            employment_type=pos.get("employment_type") or "",
            workplace=pos.get("workplace_type") or "",
        )

    return [job for job in ctx.pmap(detail, items) if job]


def avature(company: dict, ctx) -> list[dict]:
    base = company["careers_url"].rstrip("/")
    found = {}
    for kw in ctx.keywords:
        url = f"{base}/{urllib.parse.quote(kw)}/feed/"
        text = _fetch_text(url, headers={"Accept": "*/*"}, blocked_reason=True)
        items = _xml_items(text)
        if not items:
            page = _fetch_text(f"{base}/{urllib.parse.quote(kw)}", blocked_reason=True)
            for m in re.finditer(r'href="([^"]*/JobDetail/[^"]+)"[^>]*>(.*?)</a>', page, re.I | re.S):
                link = _abs(m.group(1), base)
                found.setdefault(_job_id_from_url(link), {"id": _job_id_from_url(link), "title": _strip_tags(m.group(2)), "url": link, "location": "", "posted": ""})
            continue
        for item in items[:ctx.max_per_query]:
            link = _tag(item, "link")
            title = _tag(item, "title")
            found.setdefault(_job_id_from_url(link), {"id": _job_id_from_url(link), "title": title, "url": link, "location": "", "posted": iso_date(_tag(item, "pubDate"))})
    if not found:
        raise Unsupported("no jobs found in Avature search")

    items = _prefiltered(ctx, list(found.values()))[:ctx.max_details]

    def detail(item):
        page = _fetch_text(item["url"], blocked_reason=True)
        fields = _fields_from_labels(page)
        ld = (_json_ld(page) or [{}])[0]
        desc = (
            _section_value(page, "Description")
            or _inner(r'<div[^>]+class="[^"]*tf_replaceFieldVideoTokens[^"]*"[^>]*>.*?<div[^>]+class="[^"]*article__content__view__field__value[^"]*"[^>]*>(.*?)(?:</article>)', page)
            or _inner(r'<meta name="og:description" content="([^"]+)"', page)
        )
        city = _strip_tags(fields.get("city", ""))
        country = _strip_tags(fields.get("country/region", ""))
        location = join_unique([city, country], ", ") or _strip_tags(fields.get("location(s)", "")) or _strip_tags(fields.get("work location", "")) or item["location"]
        posted = _date(ld.get("datePosted")) or _date(fields.get("posted since")) or _date(item["posted"])
        return make_job(
            item["id"],
            ld.get("title") or _strip_tags(fields.get("job name", "")) or _first(r'<meta property="og:title" content="([^"]+)"', page) or item["title"],
            item["url"],
            location=location,
            posted=posted,
            description_html=desc,
            pay=_pay_from_text(fields.get("salary range", "")),
            employment_type=_strip_tags(fields.get("employment type", "")),
            workplace=_strip_tags(fields.get("work mode", "")),
        )

    return [job for job in ctx.pmap(detail, items) if job]


def radancy(company: dict, ctx) -> list[dict]:
    base = company["careers_url"].rstrip("/")
    root = "{uri.scheme}://{uri.netloc}".format(uri=urllib.parse.urlparse(base))
    found = {}
    for kw in ctx.keywords:
        page_no = 1
        while len([j for j in found.values() if j.get("kw") == kw]) < ctx.max_per_query:
            url = f"{base}/{urllib.parse.quote(kw)}" + (f"?p={page_no}" if page_no > 1 else "")
            text = _fetch_text(url, blocked_reason=True)
            blocks = re.findall(r'<li[^>]+class="[^"]*job-card[^"]*"[^>]*>(.*?)</li>', text, re.I | re.S)
            blocks += re.findall(r'<li[^>]+class="[^"]*search-results-list__list-item[^"]*"[^>]*>(.*?)</li>', text, re.I | re.S)
            if not blocks:
                break
            before = len(found)
            for block in blocks:
                href = _first(r'href="([^"]*/job/[^"]+)"', block)
                job_id = _first(r'data-job-id="([^"]+)"', block) or _job_id_from_url(href)
                title = _strip_tags(_first(r'job-card__title[^>]*>(.*?)</a>', block) or _first(r'<h2[^>]*>(.*?)</h2>', block))
                location = _strip_tags(_first(r'<span[^>]+class="location"[^>]*>(.*?)</span>', block) or _first(r'<span[^>]+class="job-location"[^>]*>(.*?)</span>', block))
                if href and title:
                    found.setdefault(job_id, {"id": job_id, "title": title, "location": location, "url": _abs(href, root), "kw": kw})
                if len([j for j in found.values() if j.get("kw") == kw]) >= ctx.max_per_query:
                    break
            if len(found) == before:
                break
            page_no += 1
            if page_no > 3:
                break
    if not found:
        raise Unsupported("job list loads with JavaScript")

    items = _prefiltered(ctx, list(found.values()))[:ctx.max_details]

    def detail(item):
        page = _fetch_text(item["url"], blocked_reason=True)
        ld = (_json_ld(page) or [{}])[0]
        desc = ld.get("description") or _inner(r'<div[^>]+class="[^"]*job-description[^"]*"[^>]*>(.*?)(?:</section>|</main>)', page)
        return make_job(
            item["id"],
            ld.get("title") or item["title"],
            item["url"],
            location=_ld_location(ld.get("jobLocation")) or item["location"],
            posted=iso_date(ld.get("datePosted")),
            description_html=desc,
            pay=_pay_from_struct(ld.get("baseSalary")),
            employment_type=ld.get("employmentType") or "",
        )

    return [job for job in ctx.pmap(detail, items) if job]


def successfactors(company: dict, ctx) -> list[dict]:
    root = "{uri.scheme}://{uri.netloc}".format(uri=urllib.parse.urlparse(company["careers_url"]))
    found = {}
    for kw in ctx.keywords:
        url = f"{root}/services/rss/job/?locale=en_US&keywords=({urllib.parse.quote(kw)})"
        text = _fetch_text(url, headers={"Accept": "*/*"}, blocked_reason=True)
        for item in _xml_items(text)[:ctx.max_per_query]:
            link = _tag(item, "link")
            title = _tag(item, "title")
            found.setdefault(_job_id_from_url(urllib.parse.urlparse(link).path), {
                "id": _job_id_from_url(urllib.parse.urlparse(link).path),
                "title": title,
                "url": link,
                "location": "",
                "posted": iso_date(_tag(item, "pubDate")),
                "rss_description": _tag(item, "description"),
            })
    if not found:
        raise Unsupported("no jobs found in SuccessFactors RSS feed")

    items = _prefiltered(ctx, list(found.values()))[:ctx.max_details]

    def detail(item):
        page = _fetch_text(item["url"], blocked_reason=True)
        title = _first(r'<meta property="og:title" content="([^"]+)"', page) or item["title"]
        desc = _inner(r'Job Description:\s*</span>\s*<span[^>]*>(.*?)(?:</span>\s*</div>\s*</div>\s*</div>\s*</div>\s*<style|<div class="applylink)', page) or item["rss_description"]
        location = _first(r'<b>\s*Location\s*</b>\s*:\s*([^<]+)', desc) or _first(r'Location:\s*</span>\s*<span[^>]*>(.*?)</span>', page)
        apply = _first(r'<a[^>]+class="[^"]*apply[^"]*"[^>]+href="([^"]+)"', page)
        return make_job(
            item["id"],
            title.replace(" Job Details | Seagate Technology", ""),
            item["url"],
            apply_url=_abs(apply, root) if apply else item["url"],
            location=location,
            posted=item["posted"],
            description_html=desc,
        )

    return [job for job in ctx.pmap(detail, items) if job]


def _generic_ayar(company, ctx, text):
    data = _decode_json_after(text, "window.__AYAR_CAREER_JOBS__ =")
    if not isinstance(data, list) or not data:
        return None
    items = []
    for job in data:
        location = join_unique([job.get("city"), job.get("state"), job.get("country")], ", ")
        items.append({"raw": job, "id": job.get("id"), "title": job.get("title"), "location": location})
    items = _prefiltered(ctx, items)[:ctx.max_details]

    def detail(item):
        job = item["raw"]
        url = job.get("url") or f"{company['careers_url']}?gnk=job&gni={job.get('id')}"
        return make_job(
            job.get("id"),
            job.get("title"),
            _abs(url, company["careers_url"]),
            apply_url=job.get("applyUrl") or _abs(url, company["careers_url"]),
            location=item["location"],
            posted=iso_date(job.get("datePosted")),
            description_html=job.get("description") or job.get("summary") or "",
            employment_type=job.get("employmentType") or "",
        )

    return [job for job in ctx.pmap(detail, items) if job]


def _generic_credo(company, ctx, text):
    rows = re.findall(r'<tr[^>]+class="[^"]*job-main-row[^"]*"[^>]*>(.*?)(?=<tr[^>]+class="[^"]*job-main-row|\Z)', text, re.I | re.S)
    items = []
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.I | re.S)
        if len(cells) < 3:
            continue
        title = _strip_tags(cells[0])
        location = _strip_tags(cells[2])
        href = _first(r'href="([^"]+)"', row)
        if title and href:
            items.append({"id": _job_id_from_url(href.replace("/apply", "")), "title": title, "location": location, "url": _abs(href, company["careers_url"]), "html": row})
    if not items:
        return None
    items = _prefiltered(ctx, items)[:ctx.max_details]

    def detail(item):
        desc = _inner(r'<td[^>]+colspan="[^"]*"[^>]*>(.*?)</td>', item["html"]) or item["html"]
        return make_job(item["id"], item["title"], item["url"], apply_url=item["url"], location=item["location"], description_html=desc)

    return [job for job in ctx.pmap(detail, items) if job]


def _generic_ceva(company, ctx, text):
    items = []
    for m in re.finditer(r'<div[^>]+class="[^"]*item[^"]*"[^>]*>\s*<a[^>]+href="([^"]*/career/position/([^"/]+)/?[^"]*)"[^>]*>(.*?)</a>\s*<p[^>]+class="[^"]*city[^"]*"[^>]*>(.*?)</p>', text, re.I | re.S):
        title = _strip_tags(m.group(3))
        if re.search(r"still apply", title, re.I):
            continue
        items.append({"id": m.group(2), "title": title, "url": _abs(m.group(1), company["careers_url"]), "location": _strip_tags(m.group(4))})
    if not items:
        return None
    items = _prefiltered(ctx, items)[:ctx.max_details]

    def detail(item):
        page = _fetch_text(item["url"], blocked_reason=True)
        main = _inner(r'<main[^>]*>(.*?)(?:</main>)', page) or page
        title = _first(r'<h1[^>]*>(.*?)</h1>', main) or item["title"]
        return make_job(item["id"], title, item["url"], location=item["location"], description=lines_to_text(html_lines(main)))

    return [job for job in ctx.pmap(detail, items) if job]


def _generic_links(company, ctx, text):
    base = company["careers_url"]
    items = []
    for m in re.finditer(r'<a[^>]+href="([^"]*(?:/career/position/|gnk=job|careers\.hibob\.com/jobs/)[^"]*)"[^>]*>(.*?)</a>', text, re.I | re.S):
        title = _strip_tags(m.group(2))
        href = _abs(m.group(1), base)
        if len(title) < 4 or re.search(r"apply now|learn more|view|still apply", title, re.I):
            continue
        items.append({"id": _job_id_from_url(href.replace("/apply", "")), "title": title, "url": href, "location": ""})
    dedup = {}
    for item in items:
        dedup.setdefault(item["id"], item)
    if not dedup:
        return None
    items = _prefiltered(ctx, list(dedup.values()))[:ctx.max_details]

    def detail(item):
        page = _fetch_text(item["url"], blocked_reason=True)
        ld = (_json_ld(page) or [{}])[0]
        desc = ld.get("description") or lines_to_text(html_lines(_inner(r'<main[^>]*>(.*?)</main>', page) or page))
        return make_job(item["id"], ld.get("title") or item["title"], item["url"], location=_ld_location(ld.get("jobLocation")), posted=iso_date(ld.get("datePosted")), description_html=desc if "<" in desc else "", description="" if "<" in desc else desc)

    return [job for job in ctx.pmap(detail, items) if job]


def generic(company: dict, ctx) -> list[dict]:
    host = urllib.parse.urlparse(company["careers_url"]).netloc.lower()
    path = urllib.parse.urlparse(company["careers_url"]).path.lower()

    if "mediatek.com" in host or "solidigm.com" in host or "ibm.com" in host:
        raise Unsupported("job list loads with JavaScript")
    if "cognichip.ai" in host or "cornelis.com" in host:
        raise Unsupported("no readable job postings found on careers page")

    text = _fetch_text(company["careers_url"], blocked_reason=True)

    jobs = []
    if "ayarlabs.com" in host:
        jobs = _generic_ayar(company, ctx, text)
    elif "credosemi.com" in host:
        jobs = _generic_credo(company, ctx, text)
    elif "ceva-ip.com" in host and "/career/location/" in path:
        jobs = _generic_ceva(company, ctx, text)
    else:
        jobs = _generic_links(company, ctx, text)
    if jobs is None:  # the page had no job listings at all (an empty list just means none matched)
        raise Unsupported("no job-like links found on careers page")
    return jobs
