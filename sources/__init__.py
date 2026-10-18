"""Hiring-platform adapters. detect() picks one from a company's careers URL; get() loads it.

Each adapter is a function fetch(company: dict, ctx: common.Context) -> list[job dict]
(see common.make_job). Adapters raise common.Unsupported when a site can't be read.
"""
import importlib
import urllib.parse

from .common import Unsupported

# platform -> (module, function)
ADAPTERS = {
    "workday": ("workday", "fetch"),
    "greenhouse": ("ats", "greenhouse"),
    "lever": ("ats", "lever"),
    "ashby": ("ats", "ashby"),
    "smartrecruiters": ("ats", "smartrecruiters"),
    "eightfold": ("enterprise", "eightfold"),
    "oracle": ("enterprise", "oracle"),
    "jibe": ("enterprise", "jibe"),
    "phenom": ("enterprise", "phenom"),
    "amazon": ("bigtech", "amazon"),
    "apple": ("bigtech", "apple"),
    "google": ("bigtech", "google"),
    "rippling": ("pages", "rippling"),
    "jobvite": ("pages", "jobvite"),
    "comeet": ("pages", "comeet"),
    "avature": ("pages", "avature"),
    "radancy": ("pages", "radancy"),
    "successfactors": ("pages", "successfactors"),
    "page": ("pages", "generic"),
}

BLOCKED_PLATFORMS = {"icims": "iCIMS job boards block automated requests"}


def detect(url):
    """Guess the hiring platform behind a careers URL."""
    p = urllib.parse.urlparse(url or "")
    host, path = p.netloc.lower(), p.path
    if not host:
        return None
    labels = host.split(".")
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        return "workday"
    if host.endswith("greenhouse.io"):
        return "greenhouse"
    if host.endswith("lever.co"):
        return "lever"
    if host.endswith("ashbyhq.com"):
        return "ashby"
    if host.endswith("smartrecruiters.com"):
        return "smartrecruiters"
    if host == "ats.rippling.com":
        return "rippling"
    if host == "jobs.jobvite.com":
        return "jobvite"
    if host.endswith(("comeet.com", "comeet.co")):
        return "comeet"
    if "oraclecloud.com" in host or "/hcmUI/CandidateExperience" in path:
        return "oracle"
    if host.endswith("icims.com"):
        return "icims"
    if host.endswith("amazon.jobs"):
        return "amazon"
    if host == "jobs.apple.com":
        return "apple"
    if host.endswith("google.com") and "/careers" in path:
        return "google"
    if host.endswith("eightfold.ai") or (
            path.rstrip("/") == "/careers" and len(labels) >= 3 and labels[0] in ("careers", "jobs", "apply")):
        return "eightfold"
    if "/careers-home/jobs" in path:
        return "jibe"
    if "/search-results" in path:
        return "phenom"
    if "/search-jobs" in path:
        return "radancy"
    if "searchjobs" in path.lower():
        return "avature"
    if path.rstrip("/").endswith("/search") and "careers" in host:
        return "successfactors"
    return "page"


def get(platform):
    """Return the adapter function for a platform, or raise Unsupported."""
    if platform in BLOCKED_PLATFORMS:
        raise Unsupported(BLOCKED_PLATFORMS[platform])
    if platform not in ADAPTERS:
        raise Unsupported(f"no adapter for platform '{platform}'")
    module, func = ADAPTERS[platform]
    try:
        mod = importlib.import_module(f"{__name__}.{module}")
    except ModuleNotFoundError as e:
        raise Unsupported(f"adapter module '{module}' is missing") from e
    return getattr(mod, func)
