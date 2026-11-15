# Liubov (Luba) Samborska — academic website

An academic homepage with your profile photo and a publication list refreshed from arXiv. The website is static HTML and CSS; visitors do not need JavaScript. The updater uses Python's standard library and needs no API key or arXiv login.

## Turn on automatic updates on GitHub Pages

**This version uses GitHub Actions. Change the Pages source from “Deploy from a branch” to “GitHub Actions.”**

1. Download and extract `luba-samborska-website.zip`.
2. Open your existing website repository on GitHub. If you do not have one yet, create a public repository named `YOUR-USERNAME.github.io`, replacing `YOUR-USERNAME` with your GitHub username.
3. Upload the extracted files and folders to the repository's top level, replacing the previous files. Include **`.github`**, **`scripts`**, **`tests`**, **`arxiv-config.json`**, and **`publications.json`**, as well as `index.html`, `styles.css`, `favicon.svg`, `profile.jpg`, and `.nojekyll`. Upload the contents, not the ZIP or its enclosing folder.
4. Confirm that `.github/workflows/update-arxiv.yml` exists in your repository. If your upload skipped `.github`, use **Add file → Create new file**, enter `.github/workflows/update-arxiv.yml` as its name, paste the contents of that file from the ZIP, and commit it.
5. Open **Settings → Pages**. Under **Build and deployment → Source**, select **GitHub Actions**.
6. Open the **Actions** tab. Enable workflows if GitHub asks. Select **Update arXiv and publish website → Run workflow**, using your default branch.
7. Wait for both **build** and **deploy** to finish successfully. Your address is `https://YOUR-USERNAME.github.io/`.

The workflow refreshes the publication list and deploys the result every day at **13:17 UTC** (8:17 a.m. Eastern Standard Time / 9:17 a.m. Eastern Daylight Time). It also runs when you push changes to the default branch and when you choose **Run workflow**. No personal access token is required.

If arXiv is temporarily unavailable, the run fails before deployment, so the live website keeps its previous list. You can retry from the Actions tab, or let the next daily run try again.

GitHub can delay scheduled jobs and pauses scheduled workflows in public repositories after 60 days without repository activity. If that happens, re-enable the workflow in **Actions**. If you use a nonstandard branch protection policy, ensure the `github-pages` environment permits your default branch to deploy.

These files prepare automatic updates for GitHub; downloading them does not configure your GitHub account. The separately hosted review website uses its own scheduled update task.

Official GitHub instructions:

- [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [Scheduled workflows and their limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## What updates automatically

- New arXiv papers with the author name **Luba Samborska** or **Liubov Samborska**.
- Titles, author lists, publication metadata, PDF/arXiv links, and BibTeX citations.
- Results are sorted by original submission date, newest first. Multiple versions of one paper appear as one entry.
- Your custom **In Submission** and **FOCS 2026** labels, along with the flow–cut gap description, are preserved.
- Topic/category badges are omitted from the page; arXiv categories remain available in BibTeX citations.
- Papers that temporarily disappear from the API results remain in the saved list. Explicit removals can be made through `exclude_ids` in the configuration.

Updates become available after arXiv exposes them through its public API. The updater queries `au:Samborska`, then checks the full author names to exclude other people with the same surname. If you publish using a different form of your name, add it to `author_names` in `arxiv-config.json`.

**Acceptance status is not detected automatically.** The custom **In Submission** label stays until you change its `venue` value in `arxiv-config.json`. After acceptance, replace it with the confirmed conference or journal name. The updater reads publication references from arXiv when available, but does not infer acceptance from announcements or arXiv comments, and it never replaces custom venue labels.

## Edit your website

- **Bio, name, and contact:** edit `index.html` outside the `ARXIV:START` / `ARXIV:END` comments. The updater changes only the content between those two comments.
- **Profile photo:** replace `profile.jpg`, updating its width/height attributes in `index.html` if necessary.
- **Colors, fonts, and spacing:** edit `styles.css`.
- **Conference labels, descriptions, and extra links:** edit the paper's entry under `overrides` in `arxiv-config.json`, keyed by its arXiv ID. `description_html` is trusted local HTML; only put your own markup there. Conference links must use HTTPS. Do not edit generated paper entries directly because the next update replaces them.
- **Hide a paper:** add its unversioned arXiv ID to `exclude_ids`.
- **Update interval:** edit the `cron` expression in `.github/workflows/update-arxiv.yml`.

The `publications.json` file is the saved metadata snapshot. The GitHub workflow fetches current metadata into the deployment, without committing generated changes back into your source branch. On an unsuccessful refresh, the already deployed website remains untouched.

## Run locally (optional)

Python 3.11 or newer is sufficient; no packages need to be installed.

```bash
python scripts/update_publications.py
python -m unittest discover -s tests -v
```

To render only the saved metadata without contacting arXiv:

```bash
python scripts/update_publications.py --offline
```

The script prints `changed: true` or `changed: false` and the resulting paper count. Open `index.html` in a browser to preview it. In the hosted source repository, website files live under `dist/`; the updater detects that layout automatically. The downloaded GitHub package puts those files at the repository root.

The updater uses the [official arXiv Atom API](https://info.arxiv.org/help/api/user-manual.html), spaces requests by at least 3.2 seconds, and preserves the current files if a request fails, a response is malformed, or no matching papers are returned.
