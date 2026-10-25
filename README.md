# Caltrans Bid Finder

Finds Caltrans projects that fit an electrical contractor, before and after they
advertise, and shows who wins them.

- **Projects:** advertised projects, plus upcoming ones that are scheduled but not
  yet advertised (marked *Coming soon*). Filter by any of the 12 districts, by
  estimate, by electrical work, and by bid date. Electrical work is found in the
  description and in the List of Bid Items. Every column sorts, and you can set a
  status on each project.
- **Bid results:** recently opened bids, from the Caltrans Bid Summaries. Shows
  every bidder and their total, the engineer's estimate, what the low bidder carried
  for electrical items, and which electrical subcontractors each bidder listed.
  A competitor table summarizes who bids and who wins.
- **Email alerts (demo mode):** one email per new matching project. Emails are
  saved in the app and not sent.

Out of the box it shows Districts 07/08 at $1M–$3M.

## Run it locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m scraper.run manual    # scrape once (a few minutes the first time)
.venv/bin/python app.py                   # open http://127.0.0.1:5050
```

Locally the data lives in SQLite at `data/bids.db`.

## Settings (environment variables)

| Variable | Purpose |
|---|---|
| `BID_FINDER_COMPANY` | Your company name. It's added to the User-Agent and highlighted ("YOU") in bid results. |
| `BID_FINDER_CONTACT` | An email or phone in the User-Agent, so Caltrans can reach you. |
| `DATABASE_URL` | Postgres connection string. Required on Vercel. |
| `CRON_SECRET` | Protects the daily scrape endpoint on Vercel. |
| `BID_FINDER_ALERT_TO` | Recipient shown on the demo alert emails. |

The default districts, estimate range and keyword list are in `config.py`.

## Deploy on Vercel

1. Import the repo into Vercel. Flask is detected from `app.py`.
2. Add a Postgres database from the Vercel Marketplace (Neon). This sets
   `DATABASE_URL`. Without it the app falls back to a temporary SQLite file
   that is wiped whenever the function restarts.
3. Set `CRON_SECRET` (any random string) and, optionally, `BID_FINDER_COMPANY`
   and `BID_FINDER_CONTACT`.
4. Deploy. `vercel.json` schedules the daily scrape at 16:30 UTC, which is
   9:30 a.m. Pacific in summer and 8:30 a.m. in winter. Click **Refresh now**
   once to load data right away.

On a hosted deployment, **Refresh now** runs at most once every 15 minutes, so
visitors can't make the site hit Caltrans repeatedly. Each run stops optional
downloads after 4 minutes, within Vercel's 5-minute function limit, and the next
run continues where it stopped.

## Run the daily scrape on a Mac instead (optional)

Edit the paths in `deploy/com.caltransbidfinder.scrape.plist`, then:

```sh
cp deploy/com.caltransbidfinder.scrape.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.caltransbidfinder.scrape.plist
```

## How it works

- **Source.** Contractors Corner is a ServiceNow portal. The scraper reads its
  page JSON (`/api/now/sp/page`) for the advertised list (`timespan=all`), the
  upcoming list (`timespan=upcoming`) and each project's bid items. It downloads
  the public Bid Summary PDFs for results.
- **Per scrape.**
  - Two list requests.
  - The bid items for each project, fetched once, then again only after an
    addendum.
  - Each new Bid Summary PDF, fetched once.
  - Requests are 3 seconds apart.
- **Electrical detection:**
  - Keywords in the description, including Caltrans abbreviations such as "Sig",
    "Ltg" and "Elect".
  - Bid items in Standard Specifications sections 86/87 (Electrical Systems).
  - Routine items such as 870009 (maintaining existing TMS) are shown but don't
    flag a project by themselves.
- **Bid Summary PDFs.**
  - Read with `pypdf`. A+B (cost + time) contracts are supported.
  - Some summaries are posted as scanned images. Those are listed with a link to
    the PDF instead of parsed.
- **Storage.** Every project seen is stored, so changing a filter needs no
  re-scrape. Statuses, alerts and bid results persist. The same SQL runs on
  SQLite and Postgres (`db.py`).

## Email alerts (demo mode)

- **When an email is created:** after each scrape, for each project that matches
  the alert filter. Each project gets one email, never two. It waits until the
  bid items are checked and is skipped once the bid has opened. Upcoming projects
  get an early "Coming soon" email.
- **Alert filter:** set on the Projects page with *Use current filters for
  alerts*.
- **Before switching to real sending:**
  - Add an SMTP/Resend sender.
  - Set `BID_FINDER_ALERT_TO`.
  - Mark the projects already listed as alerted, so the first real run doesn't
    email the backlog.

## Notes

- The site's `robots.txt` disallows automated access. This tool reads it once a
  day at very low volume with an identifying User-Agent. Revisit this if Caltrans
  objects or publishes a feed.
- The advertised list API caps at 100 projects. The page warns if a scrape
  reaches the cap.
- Bid result history starts with the last six weeks Caltrans lists, and grows
  daily.

## Tests

```sh
.venv/bin/pytest
```

Tests run against real responses and Bid Summary PDFs saved in `tests/fixtures/`.
