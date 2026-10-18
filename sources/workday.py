"""Workday career sites (*.myworkdayjobs.com) via the JSON API their own pages use."""
import re
import urllib.parse

from .common import FetchError, get_json, iso_date, join_unique, make_job, post_json

PAGE = 20  # Workday's maximum page size


def _site(url):
    """Return (host, tenant, site) from a Workday careers URL."""
    p = urllib.parse.urlparse(url)
    parts = [s for s in p.path.split("/") if s and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", s)]
    if p.netloc.endswith("myworkdaysite.com"):  # wd3.myworkdaysite.com/recruiting/<tenant>/<site>
        i = parts.index("recruiting")
        return p.netloc, parts[i + 1], parts[i + 2]
    return p.netloc, p.netloc.split(".")[0], parts[0]


def fetch(company, ctx):
    host, tenant, site = _site(company["careers_url"])
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    found = {}
    try:
        for kw in ctx.keywords:
            total = None
            for offset in range(0, ctx.max_per_query, PAGE):
                page = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": PAGE, "offset": offset,
                                                 "searchText": kw})
                posts = page.get("jobPostings") or []
                before = len(found)
                for post in posts:
                    if post.get("externalPath"):
                        found.setdefault(post["externalPath"], post)
                if total is None:
                    total = page.get("total") or 0  # only the first page reports the real total
                # Keywords overlap heavily; once a page brings nothing new, later pages won't either.
                if len(found) == before or len(posts) < PAGE or offset + PAGE >= total:
                    break
    except FetchError as e:
        if not found:
            raise
        ctx.log(f"search stopped early ({e}); using the results fetched so far")
    hits = [p for p in found.values() if ctx.prefilter(p.get("title", ""), p.get("locationsText", ""))]

    def detail(post):
        info = get_json(api + post["externalPath"]).get("jobPostingInfo") or {}
        location = join_unique([info.get("location")] + list(info.get("additionalLocations") or []))
        country = (info.get("country") or {}).get("descriptor", "")
        if country and "," not in location:  # most tenants already write "US, CA, Santa Clara"
            location = join_unique([location, country], ", ")
        url = info.get("externalUrl") or f"https://{host}/en-US/{site}{post['externalPath']}"
        return make_job(info.get("jobReqId") or post["externalPath"], info.get("title") or post.get("title", ""),
                        url, url.rstrip("/") + "/apply", location or post.get("locationsText", ""),
                        iso_date(info.get("startDate") or info.get("postedOn") or post.get("postedOn")),
                        info.get("jobDescription") or "", employment_type=info.get("timeType", ""),
                        workplace=info.get("remoteType") or "")

    return [j for j in ctx.pmap(detail, hits[: ctx.max_details]) if j]
