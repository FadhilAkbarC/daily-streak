# daily-streak
Simple daily streak tracker for building consistent habits.

It computes streaks from full account history (starting at account creation date), then overlays recent push events so same-day activity appears faster.

Daily Streak will appear in Profile by following these steps.

## Recommended image URL

Use the branch that your workflow actively updates:

```text
https://raw.githubusercontent.com/username/daily-streak/main/assets/github-streak.svg
```

## Project structure

```text
.
├── .github/workflows/update-streaks.yml
├── assets/github-streak.svg
├── examples/sample_events.json
└── src/daily_streak.py
```

## Setup

### 1) Repository variables
Path: **Settings → Secrets and variables → Actions → Variables**

Required:
- `STREAK_USERNAME` (target GitHub username)

Optional:
- `STREAK_TIMEZONE_OFFSET` (default: `0`)
- `STREAK_MAX_PAGES` (default: `5`, clamped to `1..10`)
- `STREAK_LOOKBACK_DAYS` (default: `45`)

### 2) Repository secrets
Path: **Settings → Secrets and variables → Actions → Secrets**

No extra secret is required for default usage. The workflow uses `secrets.GITHUB_TOKEN`.

### 3) Workflow permissions
Path: **Settings → Actions → General → Workflow permissions**

Set to **Read and write permissions** so `github-actions[bot]` can commit `assets/github-streak.svg`.

### 4) First run
- Open **Actions**
- Select **Update Daily Streak Card**
- Click **Run workflow**

### 5) Embed in profile README

```md
![DailyStreaks](https://raw.githubusercontent.com/username/daily_streak/main/assets/github-streak.svg)
```

## Local usage

Online mode:

```bash
python3 src/daily_streak.py \
  --username yourusername \
  --timezone-offset 7 \
  --max-pages 5 \
  --lookback-days 45 \
  --output assets/github-streak.svg
```

Offline mode:

```bash
python3 src/daily_streak.py \
  --username demo \
  --events-file examples/sample_events.json \
  --output assets/github-streak.svg
```
