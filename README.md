# AppGrab

Finds **ASIC Design Verification** roles on the job boards listed in [companies.json](./companies.json) and shows them on one page, with location, pay (when posted), key requirements, and a direct **Apply** link for each role. The page shows new-grad and ≤1-year roles by default, and a dropdown switches to more senior levels.

## Run it

Requires Python 3.9 or newer. There's nothing to install; it only uses the standard library.

```powershell
cd AppGrab
python appgrab.py
```

A full run takes a few minutes. Progress is printed per company, then `jobs.html` opens in your browser. Re-run it whenever you want fresh results.

## Shared link

The same page is published at **https://dylwon.github.io/appgrab/** for anyone to open. GitHub runs the search on its own servers (`.github/workflows/refresh.yml`):

- every day at about 6 AM Pacific,
- whenever a change is pushed to `main` (for example an edited `companies.json`),
- or on demand: on GitHub, open **Actions → Refresh job board → Run workflow**.

Each viewer's **Applied** checkmarks are stored in their own browser. NEW badges compare against the previous cloud run, which is remembered on the `appgrab-state` branch. Some job sites block cloud servers, so a few companies can end up under "Check these sites yourself" on the shared page even when a run on your own PC reads them fine.

One-time setup, already done for this repository: it must be public, and under **Settings → Pages → Build and deployment**, **Source** must be set to **GitHub Actions**.

| Option | What it does |
|---|---|
| `--only nvidia amd` | Only search companies whose name contains one of these words (the page then lists only those) |
| `--no-open` | Don't open the page when done |
| `--out other.html` | Write the page somewhere else (default: `jobs.html`) |
| `--max-per-query 60` | Search results read per keyword per site |
| `--max-details 60` | Job postings opened per company |
| `--workers 6` | Companies searched in parallel |
| `--verbose` | Print details about postings that couldn't be read |

## Using the page

- The **level dropdown** picks which roles to show. It defaults to **New grad + ≤1 yr**, shows how many roles each option has, and remembers your choice:
  - **New grad**: the posting says new grad, NCG, university graduate, early career, etc.
  - **≤1 yr**: it asks for at most `max_years_experience` years.
  - **Not stated**: no years found in the posting (pick "New grad, ≤1 yr + not stated" to include these).
  - **2+ years**: it asks for more than `max_years_experience` years.
  - **Senior / Staff / Principal**: the title contains one of the `senior_title_terms`.
- **Company chips** filter by company type. Use the search box to filter by title, company, location, or skill (e.g. `uvm austin`).
- **Apply ↗** opens the application form where the site supports it, otherwise the posting page.
- Tick **Applied** to dim a role and hide it later with **Hide applied**. This is saved in your browser.
- **NEW** marks roles that didn't show up on your previous run. `.appgrab_seen.json` keeps track of this; delete it to start over.
- **Check these sites yourself** at the bottom lists job boards that can't be read automatically (blocked, JavaScript-only, or failed this run), with a link to each company's own search.

## Changing what it looks for

Edit the `search` block at the top of `companies.json`:

- `keywords`: search terms sent to each site. They also count as title matches.
- `entry_level_terms` and `max_years_experience`: how the experience level is decided.
- `senior_title_terms`: titles containing these words get the Senior / Staff / Principal level. To skip senior roles entirely (a slightly faster run), move these words into `exclude_title_terms`.
- `exclude_title_terms`: titles containing these words are dropped (by default managers, directors, interns, and co-ops). Remove `"intern"` and `"co-op"` to include internships.
- `locations`: `"United States"`, state codes like `"CA"`, or any text such as `"Remote"` or `"Austin"`.

To add or fix companies, see [COMPANIES_GUIDE.md](./COMPANIES_GUIDE.md). Supported platforms are detected from the URL:

- Workday, Greenhouse, Lever, Ashby, SmartRecruiters
- Eightfold, Oracle Recruiting, Phenom, Jibe, Avature, Radancy, SAP SuccessFactors
- Rippling, Jobvite, Comeet
- Amazon, Apple, Google
- Plain careers pages

To force a platform, add `"platform": "workday"` (or similar) to a company.

## How it decides what to show

1. **Is it design verification?** Titles with "verification", "DV", "UVM", "pre-silicon" or one of your keywords count. Generic hardware titles (e.g. Qualcomm's "Hardware Engineer") only count when the description is clearly about verification (UVM, testbenches, coverage, ...). Software, firmware, and physical-verification roles are dropped.
2. **Is it in a wanted location?** Multi-location roles count if any location matches. Unknown locations such as "Remote" are kept.
3. **How senior is it?** Titles with an excluded word (Manager, Director, Intern, Co-op) are dropped. Titles with a senior word (Senior, Staff, Principal, Lead, ...) get the Senior level. Everything else gets a level from the title and the years of experience the description asks for. Senior roles only count when the title itself says verification (or DV, UVM, pre-silicon), and at most 15 per company are opened, so they can't crowd out entry-level ones.

Pay comes from the site's salary fields when available, otherwise from salary ranges written in the description. Requirements are the bullets under the posting's "Requirements" or "Qualifications" heading. Both are best-effort, so check the posting before applying.

**Don't run it too often.** Running it a few times a day is plenty. Some platforms rate-limit repeated searches and temporarily answer with HTTP 403. This is most noticeable with Eightfold, which hosts Qualcomm, Micron, Infineon, STMicroelectronics and Microsoft. When it happens, the run keeps whatever it already found, so a company may show fewer roles or an error. Waiting an hour or so clears it.

## Files

| File | Purpose |
|---|---|
| `appgrab.py` | Command-line entry point |
| `filters.py` | Relevance, location, and experience-level rules |
| `sources/` | One adapter per hiring platform (`sources/__init__.py` picks the adapter from the URL) |
| `report.py`, `template.html` | Builds the results page |
| `companies.json` | Search settings and the list of companies |
| `jobs.html` | Generated results page (not committed; the shared link is built by GitHub) |
| `.appgrab_seen.json` | Remembers roles from earlier runs for the NEW badge |
| `.github/workflows/refresh.yml` | Daily cloud run that publishes the shared link |
