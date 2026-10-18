# AppGrab

Searches the job boards of the companies in [companies.json](./companies.json) for any keywords and shows the matching roles on one page, with location, pay (when posted), key requirements, and a direct **Apply** link for each role. It comes with a saved **ASIC Design Verification** search. Add your own saved searches, or run a one-off search for anything.

## Run it

Requires Python 3.9 or newer. There's nothing to install; it only uses the standard library.

```powershell
cd AppGrab
python appgrab.py                                            # the saved searches -> jobs.html
python appgrab.py --search "physical design" "place and route"   # one-off search -> its own page
python appgrab.py --search UVM PCIe --anywhere               # also match keywords inside job descriptions
```

A run takes a few minutes per search. Progress is printed per company, then the page opens in your browser. A one-off search writes its own page (e.g. `jobs-physical-design-place-and-route.html`), so `jobs.html` stays as it is.

| Option | What it does |
|---|---|
| `--search KEYWORD ...` | One-off search for these keywords instead of the saved searches |
| `--anywhere` | With `--search`: also keep roles whose description mentions a keyword, not just the title (slower) |
| `--with-saved` | With `--search`: run the saved searches too, all on one page |
| `--only nvidia amd` | Only search companies whose name contains one of these words |
| `--no-open` | Don't open the page when done |
| `--out other.html` | Write the page somewhere else |
| `--max-per-query 60` | Search results read per keyword per site |
| `--max-details 60` | Job postings opened per company per search |
| `--workers 6` | Sites searched in parallel |
| `--verbose` | Print details about postings that couldn't be read |

## Saved searches

Saved searches live under `searches` in `companies.json`. Each one gets its own entry in the page's **Search** dropdown. For example, to add physical design next to the verification search:

```json
"searches": [
  { "name": "ASIC Design Verification", "preset": "design-verification", "keywords": ["design verification", "UVM"] },
  { "name": "Physical Design", "keywords": ["physical design", "place and route", "timing closure"] }
]
```

- `name`: what the dropdown shows.
- `keywords`: sent to each site's search. A title matches when it contains all the words of one keyword, in any order ("ASICs" matches "ASIC").
- `match_description` (optional, `true`/`false`): also keep roles whose description mentions a keyword. This is useful for skills like `"UVM"` or `"PCIe"` that rarely appear in titles, but it opens more postings and is slower.
- `exclude_title_terms` (optional): extra title words to drop for this search only, e.g. `["verification"]` for an RTL design search.
- `preset` (optional): `"design-verification"` adds rules tuned for DV. It treats "DV", "UVM" and "pre-silicon" titles as matches, accepts generic titles like "Hardware Engineer" when the description is clearly about verification, and drops software, firmware and physical-verification roles.

Each saved search adds a few minutes to every run.

## Shared link

The page is published at **https://dylwon.github.io/appgrab/** for anyone to open. GitHub runs AppGrab on its own servers (`.github/workflows/refresh.yml`, which runs `cloud.py`):

- **Every day at about 6 AM Pacific** it refreshes the saved searches.
- **Anyone can search for another role from the page.** Click **+ New search**, type keywords, then click **Request search**. On the GitHub page that opens, click **Create** (a free GitHub account is needed). The search runs in about 5 minutes. The request gets a reply with a link and closes itself, and the page tracks it and switches to the results. You can also open an issue titled `Search: keyword, keyword` directly; add ` (also in descriptions)` at the end to match job descriptions too.
- **Requested searches stay in the Search dropdown for 14 days.** Asking for one again within a day just shows the existing results. At most 3 requests run at a time; the rest wait for the next run.
- **Actions → Refresh job board → Run workflow** refreshes the saved searches right away. If you type keywords in that form, it runs a one-off search instead.
- **Pushing to `main` rebuilds the page from the stored results without searching again.** For example, editing `template.html` takes effect immediately. A saved search newly added to `companies.json` is searched right away.

Results and NEW-badge history are stored on the `appgrab-state` branch. Each viewer's **Applied** checkmarks are stored in their own browser. Some job sites block cloud servers, so a few companies can end up under "Check these sites yourself" on the shared page even when a run on your own PC reads them fine.

One-time setup, already done for this repository: it must be public, and under **Settings → Pages → Build and deployment**, **Source** must be set to **GitHub Actions**.

## Using the page

- The **Search dropdown** switches between searches: saved searches first, then recently requested ones with their date. Everything below it follows the selected search, and the address (`?search=...`) links straight to it. On the shared page, **+ New search** requests another one (see above).
- The **level dropdown** picks which roles to show. It defaults to **New grad + ≤1 yr** and remembers your choice:
  - **New grad**: the posting says new grad, NCG, university graduate, early career, etc.
  - **≤1 yr**: it asks for at most `max_years_experience` years.
  - **Not stated**: no years found in the posting (pick "New grad, ≤1 yr + not stated" to include these).
  - **2+ years**: it asks for more than `max_years_experience` years.
  - **Senior / Staff / Principal**: the title contains one of the `senior_title_terms`.
- **Company chips** filter by company type. Use the filter box to narrow by title, company, location, or skill (e.g. `uvm austin`).
- **Apply ↗** opens the application form where the site supports it, otherwise the posting page.
- Tick **Applied** to dim a role and hide it later with **Hide applied**. This is saved in your browser, and it carries across searches that find the same posting.
- **NEW** marks roles that a search didn't find on its previous run. `.appgrab_seen.json` keeps track of this; delete it to start over.
- **Check these sites yourself** at the bottom lists job boards that can't be read automatically (blocked, JavaScript-only, or failed this run), with a link to each company's own search.

## Settings for every search

The `settings` block at the top of `companies.json` applies to all searches:

- `locations`: `"United States"`, state codes like `"CA"`, or any text such as `"Remote"` or `"Austin"`.
- `entry_level_terms` and `max_years_experience`: how the experience level is decided.
- `senior_title_terms`: titles containing these words get the Senior / Staff / Principal level. To skip senior roles entirely (a slightly faster run), move these words into `exclude_title_terms`.
- `exclude_title_terms`: titles containing these words are always dropped (by default managers, directors, interns, and co-ops). Remove `"intern"` and `"co-op"` to include internships.

To add or fix companies, see [COMPANIES_GUIDE.md](./COMPANIES_GUIDE.md). Supported platforms are detected from the URL:

- Workday, Greenhouse, Lever, Ashby, SmartRecruiters
- Eightfold, Oracle Recruiting, Phenom, Jibe, Avature, Radancy, SAP SuccessFactors
- Rippling, Jobvite, Comeet
- Amazon, Apple, Google
- Plain careers pages

To force a platform, add `"platform": "workday"` (or similar) to a company.

## How it works

For each company and search, AppGrab:

1. sends each keyword to the company's job board, using the same public search API the careers site itself uses, or the data embedded in its pages;
2. keeps titles that match the search and are in a wanted location (multi-location roles count if any location matches; unknown ones such as "Remote" are kept);
3. opens those postings for the full description and pulls out pay, requirements, years of experience, and the posted date;
4. assigns a level. Titles with an excluded word are dropped, and titles with a senior word get the Senior level. Everything else is leveled by the title and the years of experience the description asks for. Senior roles only count when the title itself matches, and at most 15 per company are opened, so they can't crowd out entry-level ones.

Pay comes from the site's salary fields when available, otherwise from salary ranges written in the description. Requirements are the bullets under the posting's "Requirements" or "Qualifications" heading. Both are best-effort, so check the posting before applying.

**Don't run it too often.** Running it a few times a day is plenty. Some platforms rate-limit repeated searches and temporarily answer with HTTP 403 or 429. This is most noticeable with Eightfold, which hosts Qualcomm, Micron, Infineon, STMicroelectronics and Microsoft. When it happens, AppGrab backs off and keeps whatever it already found, so a company may show fewer roles or an error. Waiting an hour or so clears it.

## Files

| File | Purpose |
|---|---|
| `appgrab.py` | Command-line entry point |
| `filters.py` | Search matching, location, and experience-level rules |
| `sources/` | One adapter per hiring platform (`sources/__init__.py` picks the adapter from the URL) |
| `report.py`, `template.html` | Builds the results page |
| `companies.json` | Settings, saved searches, and the list of companies |
| `jobs.html`, `jobs-*.html` | Generated results pages (not committed; the shared link is built by GitHub) |
| `.appgrab_seen.json` | Remembers roles from earlier runs for the NEW badge |
| `.github/workflows/refresh.yml` | Cloud runs that publish the shared link (daily, on request, and on push) |
| `cloud.py` | What a cloud run does: refresh saved searches, run requested searches, rebuild the page |
