# Putting TrialMate on GitHub

Devpost needs a public repo link, and Factory's judges read the README to see where Droid is used.

**Never upload your `.env` file. It holds your API keys.**

## Option A: GitHub Desktop (easiest, and it skips .env automatically)
1. Install GitHub Desktop from desktop.github.com and sign in.
2. File → **Add local repository** → choose the `trialmate` folder. When it says "This directory does not
   appear to be a Git repository", click **create a repository** and then **Create repository**.
3. Check the left panel: `.env` and `data` must **not** be listed. The `.gitignore` file excludes them.
4. Type a summary such as "TrialMate for HackHarvard 2026" and click **Commit to main**.
5. Click **Publish repository**, untick **Keep this code private**, then **Publish**.
6. Copy the repo URL (github.com/<you>/trialmate) into Devpost.

## Option B: upload in the browser
1. Double-click **prepare_upload.bat**. It makes a clean copy called `trialmate-github` next to your
   `trialmate` folder, without `.env` or the database.
2. On github.com, click **New repository**, name it `trialmate`, set it to **Public** and click **Create**.
3. Click **uploading an existing file** and drag everything **inside** `trialmate-github` onto the page.
4. Click **Commit changes**.

## After later changes
- GitHub Desktop: commit, then click **Push origin**.
- Browser: run prepare_upload.bat again and upload the changed files.
