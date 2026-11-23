# Setup and maintenance

Instructions for running and maintaining Hawaii Ham Radio Activity. For an overview of the project, see [README.md](README.md).

## What's in this repository

| Path | What it is |
|---|---|
| `docs/index.html` | The web page. It loads `docs/data.json`. |
| `docs/data.json` | The data for the page. It is rebuilt automatically, so don't edit it by hand. |
| `collector/collect.py` | Script that collects the data from the public sources and builds `data.json`. It needs only plain Python 3, nothing to install. |
| `history/` | Collected history (net check-ins, Reverse Beacon Network days, POTA, SOTA, FCC list). It is updated automatically. Some sources only show recent data, so this folder is what makes the month and year views possible. |
| `history/activity_days.json`, `history/achievements.json` | Permanent record of each ham's active days since the achievements started, and every achievement earned. Unlike the other history files, these are never trimmed. |
| `.github/workflows/update.yml` | Schedule that runs the collector twice a day and saves the results. |

## One-time setup (about 15 minutes)

### 1. Create the repository
1. Sign in to [github.com](https://github.com). A free account is enough.
2. Click **+ → New repository**.
3. Name it `hawaii-ham-activity`.
4. Choose **Public**, because free GitHub Pages hosting needs a public repository.
5. Click **Create repository**.

### 2. Upload the files
1. On the new repository page, click **uploading an existing file**.
2. Unzip the package. Then drag **everything inside** the unzipped folder into the browser window: `docs`, `collector`, `history`, `README.md`, `.gitignore`.
3. Click **Commit changes**.

> **The `.github` folder is hidden on Mac and Windows** and usually doesn't upload by drag and drop. Create it by hand:
> 1. Click **Add file → Create new file**.
> 2. As the file name, type `.github/workflows/update.yml`. Each `/` creates a folder.
> 3. Open `update.yml` from the zip in a text editor and paste its contents.
> 4. Click **Commit changes**.

### 3. Allow the workflow to save data
1. Go to **Settings → Actions → General**.
2. Under **Workflow permissions**, select **Read and write permissions**.
3. Click **Save**.

### 4. Turn on the website
1. Go to **Settings → Pages**.
2. Under **Build and deployment**, set Source to **Deploy from a branch**.
3. Set Branch to **main** and folder to **/docs**.
4. Click **Save**.
5. After a minute or two the page is live at `https://<your-github-username>.github.io/hawaii-ham-activity/`.

### 5. Run the first update
1. Go to the **Actions** tab. If GitHub asks, enable workflows.
2. Click **Update data → Run workflow**.
3. The first run takes about 3–5 minutes, because it downloads the FCC license database. A green check means it worked.

From then on it runs on its own at **2:15 pm and 9:45 pm Hawaii time** (00:15 and 07:45 UTC). GitHub sometimes starts scheduled runs up to an hour late; that's normal.

### 6. Add it to Google Sites
1. In the Google Sites editor, click **Insert → Embed → By URL**.
2. Paste the GitHub Pages address from step 4 and choose **Whole page**.
3. Drag the embed box to the size you want. The page scrolls inside the box.

## Self-service opt-out (Google Form)

Hams can leave the list and the achievements, or hide their name on certificates, using a Google Form. Requests are applied automatically at the next update. Nothing needs to be done by hand.

### One-time setup

1. **Create the form.** Go to [forms.google.com](https://forms.google.com) and create a blank form titled **Hawaii Ham Radio Activity – Opt-out**. Add three questions:
   - **Callsign**: short answer, required. Optional: under ⋮ → *Response validation* → *Regular expression* → *Matches* → `^[A-Za-z0-9/]{3,12}$`
   - **What would you like to do?**: multiple choice, required, with exactly these four options:
     - `Hide me from the list and achievements`
     - `Show me again in the list and achievements`
     - `Hide my name on certificates`
     - `Show my name on certificates`
   - **I confirm that I am the license holder of this callsign**: checkbox, required, one option `Yes`
2. **Don't collect email addresses** (Settings → Responses → Collect email addresses: *Do not collect*).
3. **Link a sheet:** Responses tab → *Link to Sheets* → create a new spreadsheet.
4. **Publish the responses as CSV:** in the sheet, *File → Share → Publish to web* → choose the **Form Responses 1** sheet and **Comma-separated values (.csv)** → *Publish*. Copy the link.
5. **Get the form link:** in the form, click *Send* → link icon → *Shorten URL* → copy.
6. **Add `config.json` to the repository:** on GitHub, *Add file → Create new file*, name it `config.json` (in the top folder, next to README.md), and paste:
   ```json
   {
     "optout_form_url": "PASTE THE FORM LINK HERE",
     "optout_csv_url": "PASTE THE CSV LINK HERE"
   }
   ```
   Commit, then run **Actions → Update data → Run workflow** once.

The page then shows the form link in the achievement rules and in the footer.

### Good to know
- The **latest request per callsign wins**, so a ham can undo their own request at any time.
- If the sheet can't be read, **existing opt-outs stay in place**. Nobody is shown again by accident.
- Hidden callsigns are removed from the page and the achievements. The collected history in this repository still contains the public source data, so a ham who opts back in shows up again with their full history.
- The published CSV contains only the timestamp, callsign and request. No email addresses or names.

## Day-to-day

- **Nothing to do.** Each run saves an "Data update …" commit, and the website refreshes a minute later.
- **If a source is down,** the other sources still update and the page keeps that source's last good data. The "How this works" section at the bottom of the page shows when each source last updated.
- **If a run fails,** GitHub emails you. Click the failed run on the **Actions** tab to see which step failed. Most failures are a source being temporarily unavailable and fix themselves on the next run.
- **Keeping the schedule alive:** GitHub pauses scheduled workflows in repositories with no activity for 60 days. The automatic commits should count as activity. If GitHub ever emails that the workflow was disabled, go to **Actions → Update data** and click **Enable workflow**.

## Data sources

| Source | What it gives | Refresh |
|---|---|---|
| FCC ULS amateur database | Active licenses with a Hawaii address, with the island taken from the ZIP code | Weekly |
| [40meter.net](https://40meter.net) HF net logs | Net check-ins and net control stations | Twice a day. The site only shows the current month, so new check-ins are saved here before the month resets. |
| [Reverse Beacon Network](https://www.reversebeacon.net) daily archives | CW and digital stations heard on the air (beacons excluded) | Daily, using the previous UTC day |
| [PSK Reporter](https://pskreporter.info) 6-hour archive files | FT8/FT4/JS8 and other data-mode stations transmitting from a Hawaii grid (BK/BL). WSPR is excluded. | Every run processes the newly completed 6-hour files (about 1.8 GB a day, streamed and filtered, nothing stored but Hawaii rows) |
| [POTA](https://pota.app) | Activations of Hawaii parks, with QSOs by mode | Daily (full history) |
| POTA profiles | Hunter QSOs logged by activators (the 25 most recent per ham with a POTA account) | Profiles with an account are checked twice a day. All Hawaii licensees are checked weekly for new accounts. |
| [SOTA](https://www.sota.org.uk) | Activations of Hawaii summits | Daily (full history) |

## Running it on your own computer (optional)

```
python3 collector/collect.py            # run every step
python3 collector/collect.py net40 build  # run only some steps
```
The steps are `fcc`, `net40`, `rbn`, `psk`, `pota`, `sota`, `pota_hunter`, `optout` and `build`.
