# Setting Up Cloud Scheduling via GitHub Actions

Follow these steps once. After that, picks run automatically every weekday — no Mac needed.

---

## Step 1 — Create a free GitHub account

1. Go to **github.com** → click **Sign up**
2. Enter your email, create a password, choose a username
3. Verify your email

---

## Step 2 — Create a private repository

1. Once logged in, click the **+** icon (top right) → **New repository**
2. Fill in:
   - **Repository name:** `stock-recommender`
   - **Visibility:** ✅ select **Private** (keeps your code hidden)
   - Leave everything else as default
3. Click **Create repository**
4. Leave this page open — you'll need the URL in Step 4

---

## Step 3 — Install Git on your Mac (if not already installed)

Open Terminal and run:
```
git --version
```
If you see a version number, skip to Step 4.
If you get an error, run this to install it:
```
xcode-select --install
```
Click Install in the popup. Takes ~5 minutes.

---

## Step 4 — Push your code to GitHub

In Terminal, run these commands one by one.
Replace `YOUR_GITHUB_USERNAME` with your actual GitHub username:

```bash
cd ~/karthik-claude/stock-recommender

git init
git add run.py requirements.txt tickers.txt SETUP.md GITHUB_SETUP.md .github
git commit -m "Initial stock recommender setup"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/stock-recommender.git
git push -u origin main
```

GitHub will ask for your username and password.
⚠️ For the password, use a **Personal Access Token** — not your GitHub password:
1. Go to: **github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token (classic)**
3. Give it a name, set expiry to 1 year, tick the **repo** checkbox
4. Click Generate → copy the token → paste it as your password in Terminal

---

## Step 5 — Add your API keys as GitHub Secrets

Your keys never go into the code. They're stored securely in GitHub Secrets.

1. Go to your repo on GitHub: `github.com/YOUR_USERNAME/stock-recommender`
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** for each of these — add them one by one:

| Secret Name | Value |
|---|---|
| `FINNHUB_API_KEY` | `d83uqr1r01qkm5c9ku1gd83uqr1r01qkm5c9ku20` |
| `GMAIL_ADDRESS` | `karthik.bia@gmail.com` |
| `GMAIL_APP_PASSWORD` | `snnv zvft bkuj wpbf` |

---

## Step 6 — Test it manually right now

Don't wait until tomorrow morning — trigger a run immediately:

1. Go to your repo on GitHub
2. Click the **Actions** tab
3. Click **Daily Stock Picks** in the left panel
4. Click **Run workflow** → **Run workflow** (green button)
5. Watch it run — takes about 2 minutes
6. Check your Gmail inbox — the picks email should arrive

If it worked, you're done. The schedule runs automatically from now on.

---

## Step 7 — Verify the schedule is active

1. GitHub **Actions** tab → you'll see runs appearing automatically each weekday morning
2. Each run saves a downloadable picks report under **Artifacts**
3. You'll also get the email — no need to check GitHub at all

---

## Your automatic schedule

| Day | Time (Sydney) | What happens |
|---|---|---|
| Tuesday | 8:30am AEST | Picks from Monday US close |
| Wednesday | 8:30am AEST | Picks from Tuesday US close |
| Thursday | 8:30am AEST | Picks from Wednesday US close |
| Friday | 8:30am AEST | Picks from Thursday US close |
| Saturday | 8:30am AEST | Picks from Friday US close |

---

## Updating your stock list later

When you want to add or remove tickers:
1. Edit `tickers.txt` on your Mac
2. Open Terminal and run:
```bash
cd ~/karthik-claude/stock-recommender
git add tickers.txt
git commit -m "Updated watchlist"
git push
```
GitHub picks up the change automatically on the next run.
