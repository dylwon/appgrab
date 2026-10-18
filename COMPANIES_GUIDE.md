# Adding or updating companies in `companies.json`

[companies.json](./companies.json) contains:

- the **search settings** for ASIC Design Verification roles in the US (the results page shows new grad / 0–1 years by default and can switch to senior levels), and
- **78 companies**: major chipmakers, big-tech silicon teams, EDA/IP vendors, chip startups, and AI-for-chip-design startups.

**The job-board URLs are already filled in and were checked against each site's live job listings in October 2026.** Use this guide when you want to add a company or fix a URL that stops working.

A few entries have a `notes` value starting with **"Couldn't auto-verify"**. Their URL is the company's official careers page, but the site either blocks automated requests or loads its jobs with JavaScript, so the UI may only be able to show a "search on site" link for them.

---

## 1. The fields

| Field | Required? | What goes here |
|---|---|---|
| `name` | Yes | Company name shown in the UI |
| `category` | Yes | `major`, `big-tech`, `eda-ip`, `startup`, or `ai-chip-design` (used as a filter in the UI) |
| `careers_url` | Yes | The page that lists the company's open jobs (see [section 2](#2-finding-the-urls)) |
| `search_url` | Recommended | The same page after searching for **verification**. Used as the "search on site" link if a site can't be scraped |
| `early_careers_url` | Optional | A *separate* students/new-grad job board (not just a filter on the main board) |
| `notes` | Optional | Anything unusual, e.g. "needs login" or "US roles are on a different site" |

Example entry:

```json
{
  "name": "NVIDIA",
  "category": "major",
  "careers_url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
  "search_url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite?q=verification",
  "early_careers_url": "",
  "notes": ""
}
```

**JSON rules:**

- Use double quotes only.
- Put a comma between entries, but **not after the last one**.
- No comments.
- Leave unknown fields as `""` instead of deleting them.

**Adding companies:** copy any entry and change it. Defense contractors (Lockheed Martin, Northrop Grumman, RTX, BAE Systems, L3Harris, General Dynamics Mission Systems) hire lots of entry-level ASIC/FPGA verification engineers, but usually require U.S. citizenship. Add them with `"category": "defense"` if that applies to you.

---

## 2. Finding the URLs

1. Search for **"&lt;company&gt; careers"** and open the official careers site.
2. Click **Search jobs / View all jobs / Find jobs** until you see an actual **list of postings with a search box**. Pages like "Life at X", "Benefits", or "Students" don't count.
3. Copy the address bar into `careers_url`.
   - If you're looking at a single job posting, go back to the list.
   - Delete tracking junk after the `?`, such as `utm_source=...`, `src=...`, `gh_src=...`.
4. Type **verification** into the site's search box, run the search, and copy the address bar into `search_url`.
   - Search for exactly that word. The script swaps it for other keywords.
   - If the URL didn't change after searching, leave `search_url` as `""`.
5. If the company has a separate students/new-grad job board, put it in `early_careers_url`.

**Startups:** a startup's own website often shows a job list that actually comes from Greenhouse, Lever, Ashby, etc. Click any job, then **Apply**. If the address changes to one of the platforms below, use that platform's board URL (everything before the job ID) as `careers_url`.

### What a good `careers_url` looks like

Use this to check that you've reached the real job board. You don't need to write down the platform; the script detects it from the URL.

| Platform | URL looks like |
|---|---|
| Workday | `https://<company>.wd5.myworkdayjobs.com/<SiteName>` (the `wd` number varies; `/en-US/` in the middle is fine; cut off anything from `/job/` onward), or `https://wd3.myworkdaysite.com/recruiting/<company>/<SiteName>` |
| Greenhouse | `https://job-boards.greenhouse.io/<company>` or `https://boards.greenhouse.io/<company>` |
| Lever | `https://jobs.lever.co/<company>` |
| Ashby | `https://jobs.ashbyhq.com/<company>` |
| SmartRecruiters | `https://jobs.smartrecruiters.com/<Company>` |
| Workable | `https://apply.workable.com/<company>/` |
| Oracle | `https://<code>.fa.<region>.oraclecloud.com/hcmUI/CandidateExperience/en/sites/<SITE>/requisitions` |
| Eightfold | `https://<company>.eightfold.ai/careers`, or a company domain whose search URL looks like `/careers?query=...` |
| Phenom | A company domain whose search URL contains `/search-results?keywords=...` |
| iCIMS | `https://careers-<company>.icims.com/jobs/search` |
| SuccessFactors | `https://career<N>.successfactors.com/...?company=...` or `https://jobs.<company>.com/search/?q=...` |
| Taleo | `https://<company>.taleo.net/careersection/...` |
| Small-startup platforms | `ats.rippling.com/<company>/jobs`, `<company>.bamboohr.com/careers`, `jobs.gem.com/<company>`, `<company>.applytojob.com`, `<company>.recruitee.com` |
| Company's own site (Apple, Google, Meta, Amazon, Microsoft, Tesla, ...) | Their own domain. That's fine, but make sure `search_url` is filled in for these. |

---

## 3. Search settings (already done)

The `search` block at the top of the file is set up for ASIC DV roles in the US. You only need to touch it if you want something different:

- `keywords`: what gets searched on each site.
- `entry_level_terms` and `max_years_experience`: how the script decides a role is new-grad / 0–1 years. It checks the title and the "X+ years" wording in the description.
- `senior_title_terms`: titles containing any of these (Senior, Staff, Principal, ...) are labeled senior. The results page hides them unless you pick a senior level in its dropdown.
- `exclude_title_terms`: titles containing any of these are skipped. Remove `"intern"` and `"co-op"` if you also want internships.
- `locations`: change to specific states/cities (e.g. `["CA", "TX", "Remote"]`) or add more countries.

---

## 4. Checklist after editing

- [ ] **The JSON is valid.** VS Code shows no red squiggles, or `python -m json.tool companies.json` prints the file back without an error. If there's an error, it gives you the line number.
- [ ] Every `careers_url` opens a **list** of jobs, not a single posting, a login page, or a marketing page.
- [ ] Every `search_url` you filled in shows results for "verification".
- [ ] **Acquired or shut-down companies are removed.** If a careers page redirects to a different company, delete the entry. Its roles will show up under the new parent.
- [ ] **No duplicates.** If a company genuinely has two separate boards (e.g. a US board and a global board), make two entries with different names, like `"Samsung Semiconductor (US)"`.

These were left out on purpose because they've been acquired or absorbed:

- Rivos → Meta
- Celestial AI → Marvell
- Ventana Micro Systems and Alphawave Semi → Qualcomm
- Groq's hardware team → NVIDIA

Auradine is still included under its new name, Velaura AI.

---

## 5. Optional: have an AI chatbot do the legwork

If you add a batch of new companies, you can upload `companies.json` to a chatbot that can browse the web (ChatGPT, Perplexity, Copilot) along with the prompt below. Chatbots sometimes make up URLs that look right but don't work, so still run the checklist in [section 4](#4-checklist-after-editing). The script will also flag any URL that doesn't lead to a job board.

```text
Fill in the attached companies.json. For every object in "companies" whose
"careers_url" is empty:

1. careers_url: the URL of the company's official job SEARCH page (the page that lists
   all open jobs). Not a single job posting, a login page, or a marketing page. If the
   job list is hosted by an applicant tracking system (myworkdayjobs.com, greenhouse.io,
   lever.co, ashbyhq.com, smartrecruiters.com, workable.com, oraclecloud.com,
   eightfold.ai, icims.com, etc.), use that board's root URL.
2. search_url: the URL of that same page after searching for the keyword
   "verification". Use "" if the site doesn't put the search term in the URL.
3. early_careers_url: a separate students/new-grad job board if one exists, else "".
4. notes: "" unless something is unusual. If the company was acquired or shut down,
   say so here and leave the URLs as "".

Only use URLs you actually opened and confirmed show a job list. Never guess or
construct a URL. Do not change "search", "name", or "category", and leave entries
that already have a careers_url as they are. Return only the complete, valid JSON
file with no commentary.
```

---

Once the file looks the way you want, run `python appgrab.py` to search every company (see [README.md](./README.md)).
