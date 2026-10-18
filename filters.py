"""Decide which postings match a search, whether they're in the wanted locations, and how senior they are."""
import re
import threading

from sources.common import (extract_requirements, html_lines, lines_to_text, min_years, parse_pay,
                            text_lines)

STOPWORDS = {"and", "or", "of", "the", "a", "an", "for", "in", "with", "to"}

# ---------- "design-verification" preset ----------
# Titles that are DV even when the keywords don't spell them out exactly.
DV_TITLE = re.compile(r"verif|pre-?\s?silicon|presilicon|\bDV\b|\bUVM\b", re.I)
# Hardware-flavoured titles that *might* be DV (e.g. Qualcomm's "Hardware Engineer"); they need
# verification evidence in the description to be kept.
HW_TITLE = re.compile(r"\b(asic|soc|silicon|hardware|hw|rtl|chip|digital|cpu|gpu|npu|ip|logic|design|fpga"
                      r"|emulation|memory|dram|serdes|pcie|ddr|dsp|mixed[- ]signal|ams|analog)\b", re.I)
HARD_HW_TITLE = re.compile(r"\b(asic|soc|silicon|hardware|hw|rtl|chip|cpu|gpu|fpga|ip)\b", re.I)
SOFTWARE_TITLE = re.compile(r"software|\bsw\b|firmware", re.I)
NOT_DV_TITLE = re.compile(r"physical (?:design )?verification|identity|fraud|payment|\bkyc\b|sales|marketing"
                          r"|recruit|legal|finance|clinical|regulatory|mechanical|technician", re.I)
POST_SILICON = re.compile(r"post-?\s?silicon", re.I)
HW_TEXT = ["systemverilog", "verilog", "uvm", "rtl", "asic", "soc", "silicon", "testbench", "fpga",
           "hardware", "chip"]
DV_TEXT = ["design verification", "functional verification", "verification engineer", "uvm", "testbench",
           "test bench", "functional coverage", "code coverage", "coverage closure", "constrained random",
           "constrained-random", "verification plan", "verification environment", "systemverilog assertion",
           "formal verification", "pre-silicon", "scoreboard"]


class DesignVerification:
    """Rules tuned for ASIC design-verification searches ("preset": "design-verification")."""

    @staticmethod
    def title_class(title, keyword_match):
        if NOT_DV_TITLE.search(title):
            return "reject"
        if SOFTWARE_TITLE.search(title) and not HARD_HW_TITLE.search(title):
            return "reject"
        if POST_SILICON.search(title) and not re.search(r"pre-?\s?silicon|verif", title, re.I):
            return "reject"
        if keyword_match or DV_TITLE.search(title):
            return "strong"
        return "maybe" if HW_TITLE.search(title) else "reject"

    @staticmethod
    def accepts(verdict, title, low_text):
        if verdict == "maybe":
            return sum(t in low_text for t in DV_TEXT) >= 3
        # A "verification" title with no hardware anywhere is something like identity verification.
        return not low_text or bool(HARD_HW_TITLE.search(title)) or any(t in low_text for t in HW_TEXT)


PRESETS = {"design-verification": DesignVerification}

# ---------- Locations ----------
US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts",
    "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "DC": "district of columbia"}
US_MARK = re.compile(r"\bUS\b|\bUSA\b|\bU\.S\.|united states", re.I)
US_CODE = re.compile(r"(?:^|,|-|\()\s*(" + "|".join(US_STATES) + r")\s*(?:$|,|-|\)|\d{5})")
US_NAME = re.compile(r"\b(" + "|".join(US_STATES.values()) + r")\b", re.I)
US_CITY = re.compile(
    r"\b(san jose|santa clara|sunnyvale|mountain view|palo alto|san francisco|san diego|irvine|los angeles"
    r"|austin|boston|seattle|redmond|hillsboro|beaverton|folsom|boise|raleigh|durham|morrisville|atlanta"
    r"|chandler|phoenix|tempe|fort collins|boulder|dallas|plano|richardson|houston|pittsburgh|minneapolis"
    r"|bay area|silicon valley|westford|marlborough|andover|poughkeepsie|essex junction|cupertino|milpitas"
    r"|fremont|pleasanton|menlo park|redwood city|foster city|san mateo|burlingame|carlsbad|santa barbara"
    r"|hawthorne|el segundo|kirkland|bellevue|lehi|orlando|huntsville|colorado springs|longmont"
    r"|shakopee|waltham|new york city|nyc)\b", re.I)
NON_US = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|pune|noida|chennai|ahmedabad|china|shanghai|beijing|shenzhen"
    r"|hsinchu|taiwan|taipei|israel|haifa|tel aviv|yokneam|herzliya|jerusalem|petah tikva|canada|ontario"
    r"|toronto|ottawa|kanata|markham|waterloo|vancouver|montreal|united kingdom|england|scotland"
    r"|bristol|edinburgh|london|germany|munich|dresden|berlin|france|paris|grenoble|sophia antipolis"
    r"|poland|krakow|krak\u00f3w|warsaw|ireland|dublin|cork|netherlands|eindhoven|japan|tokyo|korea"
    r"|seoul|singapore|malaysia|penang|vietnam|philippines|mexico|guadalajara|costa rica|brazil|italy"
    r"|milan|spain|sweden|finland|denmark|switzerland|austria|belgium|czech|hungary|romania|serbia"
    r"|greece|portugal|norway|australia|hong kong|thailand|egypt|turkey|armenia|ukraine)\b", re.I)
MULTI = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)


def _term_re(term, suffix=""):
    body = re.escape(term.strip()).replace(r"\ ", r"[\s-]+").replace(r"\-", r"[\s-]?")
    if term.lower().endswith("grad"):
        body += "(?:uate)?s?"
    flags = 0 if term.isupper() else re.I
    return re.compile(r"(?<![A-Za-z0-9])" + body + suffix + r"(?![A-Za-z0-9])", flags)


def _tokens(text):
    """Lower-cased words with simple plural folding, so "ASICs" matches "ASIC"."""
    text = re.sub(r"pre[\s-]+silicon", "presilicon", text.lower())
    words = []
    for w in re.findall(r"[a-z0-9+#]+", text):
        words.append(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return words


class Settings:
    """Settings shared by every search (the "settings" block of companies.json)."""

    def __init__(self, block):
        self.max_years = block.get("max_years_experience", 1)
        self.locations = block.get("locations") or []
        self.exclude = [_term_re(t, suffix=r"(?:s|ships?)?") for t in block.get("exclude_title_terms", [])]
        self.senior = [_term_re(t, suffix=r"s?") for t in block.get("senior_title_terms", [])]
        entry = block.get("entry_level_terms", [])
        # Generic words ("junior", "associate", "engineer I") only count in the title; phrases also
        # count in the description ("open to new grads").
        self.entry_title = [_term_re(t) for t in entry]
        self.entry_text = [_term_re(t) for t in entry if " " in t and not t.lower().startswith("engineer")
                           or t.isupper()]


class Search:
    """One saved or one-off search: the keywords sent to each site, and how titles are judged."""

    def __init__(self, spec, adhoc=False):
        self.keywords = [k.strip() for k in spec.get("keywords") or [] if k.strip()]
        if not self.keywords:
            raise ValueError(f"search {spec.get('name')!r} has no keywords")
        self.name = spec.get("name") or ", ".join(self.keywords)
        self.slug = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "search"
        self.adhoc = adhoc
        self.match_description = bool(spec.get("match_description"))
        self.preset = PRESETS.get(spec.get("preset") or "")
        if spec.get("preset") and not self.preset:
            raise ValueError(f"unknown preset {spec['preset']!r} (known: {', '.join(PRESETS)})")
        self.exclude = [_term_re(t, suffix=r"(?:s|ships?)?") for t in spec.get("exclude_title_terms", [])]
        self.keyword_tokens = [set(_tokens(k)) - STOPWORDS for k in self.keywords]
        self.keyword_res = [_term_re(k) for k in self.keywords]


def load(config):
    """(settings, saved searches) from companies.json. Also reads the older layout, where a single
    "search" block held both the settings and the design-verification keywords."""
    block = config.get("settings") or config.get("search") or {}
    specs = config.get("searches")
    if specs is None and block.get("keywords"):
        specs = [{"name": block.get("role") or "Saved search", "keywords": block["keywords"],
                  "preset": "design-verification"}]
    return Settings(block), [Search(spec) for spec in specs or []]


def title_class(title, s, search):
    """'strong' (title matches the search), 'maybe' (needs evidence in the description), or 'reject'."""
    if any(rx.search(title) for rx in s.exclude) or any(rx.search(title) for rx in search.exclude):
        return "reject"
    words = set(_tokens(title))
    keyword_match = any(t and t <= words for t in search.keyword_tokens)
    if search.preset:
        return search.preset.title_class(title, keyword_match)
    if keyword_match:
        return "strong"
    return "maybe" if search.match_description else "reject"


def is_senior(title, s):
    return any(rx.search(title) for rx in s.senior)


def is_us(segment):
    """True/False for a single location string, or None when it can't be told."""
    if US_MARK.search(segment):
        return True
    if NON_US.search(segment):
        return False
    if US_CODE.search(segment) or US_NAME.search(segment) or US_CITY.search(segment):
        return True
    return None


def location_ok(location, terms):
    """Whether a (possibly multi-place) location matches the configured locations. A place that is
    clearly elsewhere outweighs parts that can't be told; unknown locations ("Remote", "3 Locations",
    blank) pass so they can be checked once the job details are in."""
    if not terms:
        return True
    segments = [p.strip() for p in re.split(r";|\|", location or "") if p.strip()]
    if not segments or any(MULTI.match(seg) for seg in segments):
        return True
    statuses = [is_us(seg) for seg in segments]
    us = True if True in statuses else False if False in statuses else None
    for term in (t.strip() for t in terms):
        if term.lower() in ("united states", "us", "usa", "u.s."):
            if us is not False:
                return True
        elif len(term) == 2 and term.upper() in US_STATES:
            code = term.upper()
            if any(re.search(rf"\b{code}\b", seg) or US_STATES[code] in seg.lower() for seg in segments):
                return True
        elif any(term.lower() in seg.lower() for seg in segments):
            return True
    return False


def make_prefilter(s, search, maybe_budget=25, senior_budget=15):
    """Cheap title/location check adapters run before fetching job details. Caps how many 'maybe'
    titles (e.g. "Hardware Engineer") and senior titles a single company can send to the detail
    step, so they can't crowd entry-level roles out of the per-company limit."""
    lock = threading.Lock()
    budgets, used = {"maybe": maybe_budget, "senior": senior_budget}, {"maybe": 0, "senior": 0}

    def prefilter(title, location):
        verdict = title_class(title or "", s, search)
        if verdict == "reject" or not location_ok(location or "", s.locations):
            return False
        senior = is_senior(title or "", s)
        if senior and verdict == "maybe":
            return False  # senior roles only count when the title itself matches
        kind = "senior" if senior else verdict
        if kind in budgets:
            with lock:
                if used[kind] >= budgets[kind]:
                    return False
                used[kind] += 1
        return True
    return prefilter


def evaluate(job, s, search):
    """Return the job enriched with requirements/pay/level, or None if it isn't a wanted role."""
    verdict = title_class(job["title"], s, search)
    if verdict == "reject" or not location_ok(job["location"], s.locations):
        return None
    if verdict == "maybe" and is_senior(job["title"], s):
        return None
    lines = html_lines(job["description_html"]) if job["description_html"] else text_lines(job["description"])
    text = lines_to_text(lines) or job["description"]
    if search.preset:
        if not search.preset.accepts(verdict, job["title"], text.lower()):
            return None
    elif verdict == "maybe" and not any(rx.search(text) for rx in search.keyword_res):
        return None
    reqs = job["requirements"] or extract_requirements(lines)
    years = min_years(reqs, text)
    pay, pay_max = parse_pay(job["pay"] or text)
    if job["pay"] and not pay:
        pay = job["pay"]
    # The title is trusted outright; a "new grad" phrase in the text only counts when the posting
    # doesn't also demand more experience than allowed (boilerplate often mentions new grads).
    entry_title = any(rx.search(job["title"]) for rx in s.entry_title)
    entry_text = any(rx.search(text) for rx in s.entry_text)
    if is_senior(job["title"], s):
        level = "senior"
    elif entry_title or (entry_text and (years is None or years <= s.max_years)):
        level = "new-grad"
    elif years is None:
        level = "unspecified"
    else:
        level = "entry" if years <= s.max_years else "experienced"
    return {**job, "description": text, "requirements": reqs, "years": years, "level": level,
            "pay": pay, "pay_max": pay_max}
