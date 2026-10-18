"""Shared plumbing for the job-board adapters: HTTP, HTML-to-text, and field parsing."""
import concurrent.futures as cf
import datetime as dt
import gzip
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
MIN_INTERVAL = 0.15  # seconds between request starts to the same host, to stay under rate limits
_slots, _slots_lock = {}, threading.Lock()


def _wait_turn(url):
    host = urllib.parse.urlparse(url).netloc
    with _slots_lock:
        slot = max(time.monotonic(), _slots.get(host, 0.0))
        _slots[host] = slot + MIN_INTERVAL
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


class FetchError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Unsupported(Exception):
    """The site can't be read automatically (blocked, JavaScript-only, or unknown platform)."""


def _retry_delay(err, attempt):
    """Seconds to wait before retrying: the server's Retry-After when given, else a growing backoff
    (longer for 429 "too many requests" than for server errors)."""
    after = (err.headers.get("Retry-After") or "").strip() if err.headers else ""
    if after.isdigit():
        return min(int(after), 60)
    return (8 if err.code == 429 else 2) * (attempt + 1)


def request(url, method="GET", data=None, headers=None, timeout=30, retries=2):
    """Fetch a URL and return (final_url, text). Retries on throttling and server errors."""
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
         "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
        else:
            body = data.encode() if isinstance(data, str) else data
    for attempt in range(retries + 1):
        _wait_turn(url)
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return r.geturl(), raw.decode(r.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(_retry_delay(e, attempt))
                continue
            raise FetchError(f"HTTP {e.code} from {url}", e.code) from None
        except (urllib.error.URLError, OSError) as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise FetchError(f"{type(e).__name__} from {url}: {e}") from None
    raise FetchError(f"Gave up on {url}")


def get_text(url, **kw):
    return request(url, **kw)[1]


def get_json(url, **kw):
    text = request(url, **kw)[1]
    try:
        return json.loads(text)
    except ValueError:
        raise FetchError(f"Expected JSON from {url}") from None


def post_json(url, payload, **kw):
    return get_json(url, method="POST", data=payload, **kw)


class Context:
    """What an adapter needs from the runner: search terms, a cheap title/location filter, limits."""

    def __init__(self, keywords, prefilter=None, max_per_query=60, max_details=80, workers=4, log=None):
        self.keywords = keywords
        self.prefilter = prefilter or (lambda title, location: True)
        self.max_per_query = max_per_query
        self.max_details = max_details
        self.workers = workers
        self.log = log or (lambda msg: None)

    def pmap(self, fn, items):
        """Run fn over items with a few threads. Failed items become None so one bad posting
        doesn't sink the whole company."""
        def safe(item):
            try:
                return fn(item)
            except Exception as e:  # noqa: BLE001 - individual postings are best-effort
                self.log(f"  skipped one posting: {e}")
                return None
        with cf.ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(safe, items))


def _plain(text):
    """Collapse whitespace and drop any stray markup (titles/locations should never contain HTML)."""
    text = str(text or "")
    if "<" in text or "&" in text:
        text = html.unescape(re.sub(r"<[^>]*>?", " ", html.unescape(text)))
    return " ".join(text.split())


def make_job(id, title, url, apply_url="", location="", posted="", description_html="",
             description="", requirements=None, pay="", employment_type="", workplace=""):
    return {"id": str(id), "title": _plain(title).lstrip("\u2022\u00b7*- "), "url": url,
            "apply_url": apply_url or url, "location": _plain(location), "posted": posted or "",
            "description_html": description_html or "", "description": description or "",
            "requirements": requirements or [], "pay": pay or "",
            "employment_type": employment_type or "", "workplace": workplace or ""}


def join_unique(parts, sep="; "):
    seen = []
    for p in parts:
        p = " ".join(str(p or "").split())
        if p and p not in seen:
            seen.append(p)
    return sep.join(seen)


# ---------- HTML to structured lines ----------

_BLOCK = {"p", "div", "ul", "ol", "table", "tr", "section", "article", "header", "footer",
          "blockquote", "dd", "dt"}
_HEAD = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP = {"script", "style", "noscript", "svg", "head", "title"}
_BULLET = re.compile(r"^[\u2022\u00b7\u25aa\u25cf\u25e6\u2023\u2043\u2013*-]\s*")


class _LineParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.buf, self.kind = [], [], "p"
        self.bold = self.skip = self.chars = self.bold_chars = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
        elif tag == "li":
            self.flush()
            self.kind = "li"
        elif tag in _HEAD:
            self.flush()
            self.kind = "h"
        elif tag in _BLOCK or tag == "br":
            self.flush()
        elif tag in ("strong", "b"):
            self.bold += 1

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _HEAD or tag == "li" or tag in _BLOCK:
            self.flush()
        elif tag in ("strong", "b"):
            self.bold = max(0, self.bold - 1)

    def handle_data(self, data):
        if self.skip:
            return
        self.buf.append(data)
        n = len(data.strip())
        self.chars += n
        if self.bold:
            self.bold_chars += n

    def flush(self):
        text = " ".join("".join(self.buf).split())
        if not text:
            return  # keep the pending kind, e.g. <li><p>text</p></li>
        kind = self.kind
        m = _BULLET.match(text)
        if kind == "p" and m and len(text) > 3:
            kind, text = "li", text[m.end():]
        elif kind == "p" and len(text) <= 90 and (
                text.endswith(":") or self.bold_chars >= 0.8 * max(self.chars, 1)):
            kind = "h"
        self.lines.append((kind, text))
        self.buf, self.kind, self.chars, self.bold_chars = [], "p", 0, 0


_CAPS_HEAD = re.compile(r"\b([A-Z][A-Z0-9&/,'() -]{2,48}:)\s+")


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


def text_lines(text):
    text = text or ""
    if "\n" not in text and len(text) > 400 and len(_CAPS_HEAD.findall(text)) >= 2:
        # One long line with ALL-CAPS headings (e.g. AMD): rebuild headings and sentence "bullets".
        parts = _CAPS_HEAD.split(text)
        out = [("p", s) for s in _sentences(parts[0])]
        for head, body in zip(parts[1::2], parts[2::2]):
            out.append(("h", head.strip()))
            out += [("li", s) for s in _sentences(body)]
        return out
    out = []
    for raw in text.splitlines():
        t = " ".join(raw.split())
        if not t:
            continue
        m = _BULLET.match(t)
        if m and len(t) > 3:
            out.append(("li", t[m.end():]))
        elif t.endswith(":") and len(t) <= 90:
            out.append(("h", t))
        else:
            out.append(("p", t))
    return out


def html_lines(markup):
    """Turn HTML (or plain text) into [(kind, text)] where kind is 'h', 'li' or 'p'."""
    if not markup:
        return []
    if "<" not in markup:
        return text_lines(markup)
    p = _LineParser()
    p.feed(markup)
    p.close()
    p.flush()
    return p.lines


def lines_to_text(lines):
    out = []
    for kind, t in lines:
        out.append(("\n" + t) if kind == "h" else ("\u2022 " + t) if kind == "li" else t)
    return "\n".join(out).strip()


def clip(text, n=220):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"


# ---------- Requirements ----------

_REQ_HEAD = re.compile(
    r"qualification|requirement|what you(?:'|\u2019)?ll need|what you need|what you bring|you bring"
    r"|you have|you(?:'|\u2019)?ll have|about you|who you are|looking for|skills|experience"
    r"|must[- ]have|ideal candidate", re.I)
_PREFERRED = re.compile(r"prefer|nice[- ]to[- ]have|bonus|\bplus\b|desired|optional", re.I)
_NOT_REQ = re.compile(
    r"benefit|perk|we offer|about us|about the (?:team|company|role)|responsibilit|you(?:'|\u2019)?ll do"
    r"|day[- ]to[- ]day|compensation|salary|equal opportunity|life at", re.I)
_REQ_LINE = re.compile(
    r"\b(?:b\.?s\.?|m\.?s\.?|ph\.?d|bachelor|master|degree|years?|experience (?:with|in)|knowledge of"
    r"|proficien\w*|familiar\w*|understanding of|systemverilog|verilog|uvm)\b", re.I)


def extract_requirements(lines, limit=6):
    """Pick the bullets under the description's requirements heading (required before preferred)."""
    heads = [i for i, (k, t) in enumerate(lines)
             if k == "h" and _REQ_HEAD.search(t) and not _NOT_REQ.search(t)]
    heads.sort(key=lambda i: bool(_PREFERRED.search(lines[i][1])))
    for i in heads:
        items = []
        for kind, text in lines[i + 1:]:
            if kind == "h":
                break
            items.append(clip(text))
            if len(items) >= limit:
                break
        if items:
            return items
    return [clip(t) for k, t in lines if k == "li" and _REQ_LINE.search(t)][:limit]


# ---------- Pay ----------

_AMT = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?([kK])?"
_DASH = r"\s*(?:-|\u2013|\u2014|to|and)\s*"
_PAY_RES = [
    re.compile(r"\$\s?" + _AMT + _DASH + r"(?:US)?\$?\s?" + _AMT),
    re.compile(_AMT + r"\s*USD" + _DASH + _AMT + r"\s*USD", re.I),
    re.compile(r"USD\s*\$?\s?" + _AMT + _DASH + r"(?:USD\s*)?\$?\s?" + _AMT, re.I),
]
_BIG_UNIT = re.compile(r"\s*(?:million|billion|mm\b|m\b|b\b)", re.I)


def _amount(num, k):
    value = float(num.replace(",", ""))
    return value * 1000 if k else value


def format_pay(lo, hi, hourly=False, currency="USD"):
    """Format a salary range. Returns (display, max_annual_usd_or_0)."""
    if not lo and not hi:
        return "", 0
    lo, hi = lo or hi, hi or lo
    if not hourly and 0 < lo < 1000 <= hi:  # e.g. "$200 - $400,000" means $200K
        lo *= 1000
    sym = "$" if currency in ("USD", "", None) else f"{currency} "
    fmt = (lambda v: f"{sym}{v:,.2f}".replace(".00", "")) if hourly else (lambda v: f"{sym}{v:,.0f}")
    text = fmt(lo) if lo == hi else f"{fmt(lo)} \u2013 {fmt(hi)}"
    text += " /hr" if hourly else " /yr"
    usd = currency in ("USD", "", None)
    return text, (hi * 2080 if hourly else hi) if usd else 0


def parse_pay(text):
    """Find salary ranges in free text. Returns (display, max_annual) or ("", 0)."""
    found = []
    for rx in _PAY_RES:
        for m in rx.finditer(text or ""):
            if _BIG_UNIT.match(text, m.end()):
                continue
            lo, hi = sorted((_amount(m.group(1), m.group(2)), _amount(m.group(3), m.group(4))))
            hourly = hi < 500
            if hourly and not 10 <= lo:
                continue
            if not hourly and not (15000 <= lo and hi <= 2_000_000):
                continue
            found.append((m.start(), lo, hi, hourly))
    if not found:
        return "", 0
    found.sort()
    shown, best, seen = [], 0, set()
    for _, lo, hi, hourly in found:
        if (lo, hi) in seen:
            continue
        seen.add((lo, hi))
        text_part, annual = format_pay(lo, hi, hourly)
        best = max(best, annual)
        if len(shown) < 2:
            shown.append(text_part)
    return " \u00b7 ".join(shown) + (" \u2026" if len(seen) > 2 else ""), best


# ---------- Years of experience ----------

_WORDNUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_N = r"(\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
_YEARS = re.compile(_N + r"\s*(?:\+|plus)?\s*(?:(?:-|\u2013|to)\s*" + _N + r"\s*)?\+?\s*"
                    r"(?:or more\s+)?(?:years?|yrs?|year\(s\))\b", re.I)


def min_years(requirements, description):
    """Smallest 'N years' mentioned in the requirements, or in description sentences about experience."""
    chunks = list(requirements)
    chunks += [s for s in re.split(r"(?<=[.;\n\u2022])\s+", description or "")
               if re.search(r"experien", s, re.I)]
    best = None
    for chunk in chunks:
        for m in _YEARS.finditer(chunk):
            raw = m.group(1).lower()
            n = int(raw) if raw.isdigit() else _WORDNUM[raw]
            if n <= 25:
                best = n if best is None else min(best, n)
    return best


# ---------- Dates ----------

def iso_date(value):
    """Best-effort conversion of epoch numbers, ISO strings, or 'Posted 3 Days Ago' to YYYY-MM-DD."""
    if value in (None, ""):
        return ""
    today = dt.date.today()
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e11 else value
        return dt.datetime.fromtimestamp(secs, dt.timezone.utc).date().isoformat()
    s = str(value).strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    low = s.lower()
    if "today" in low or "just posted" in low:
        return today.isoformat()
    if "yesterday" in low:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*(hour|day|week|month)s?\s*ago", low)
    if m:
        n = int(m.group(1))
        days = {"hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[m.group(2)]
        return (today - dt.timedelta(days=days)).isoformat()
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
                "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""
