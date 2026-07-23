# Crash-to-MR AI Agent Kit

An autonomous agent that catches Firebase Crashlytics fatal-issue alerts,
investigates the affected repository with Claude, applies a fix when it's
confident, and opens a GitLab Merge Request — no human in the loop for the
first draft.

```
Production Crash
      |
      v
Firebase Crashlytics (onNewFatalIssuePublished)
      |
      v
Firebase Cloud Function (firebase-functions/index.js)
      | HTTP POST -> GitLab pipeline trigger
      v
GitLab CI/CD pipeline
      | runs .agents/skills/crash-to-mr/crash_agent.py
      v
Claude investigates the repo, patches the fix, opens a GitLab MR
```

## Install into a target repo

```
curl -fsSL https://raw.githubusercontent.com/josephshibin/crash-to-mr-kit/main/install.sh | bash
```

This drops the agent skill into `.agents/skills/crash-to-mr/` in the current
repo (must be run from a repo root).

## Configuration

> **Permission note:** creating a Pipeline trigger token, a Project Access
> Token, or CI/CD variables all require **Maintainer (or Owner) role** on the
> target GitLab project. If the **Settings** entry is missing from your
> project's left sidebar entirely, that's GitLab telling you your current
> role (e.g. Developer) is too low — GitLab hides Settings rather than
> showing it disabled. Ask a project Maintainer/Owner to either grant you
> Maintainer or create these three items and hand you the values.

### 1. GitLab CI/CD variables (target repo)
Add these under Settings > CI/CD > Variables (masked, protected):

| Variable | Purpose |
|---|---|
| `GITLAB_AGENT_TOKEN` | Project access token (`api` scope) the agent uses to create branches, commits, and MRs |
| `ANTHROPIC_API_KEY` | Claude API key |

### 2. `.gitlab-ci.yml`

```yaml
run-crash-agent:
  rules:
    - if: $CI_PIPELINE_SOURCE == "trigger" && $CRASH_PAYLOAD
  image: python:3.10-slim
  before_script:
    - pip install -r .agents/skills/crash-to-mr/requirements.txt
  script:
    - python .agents/skills/crash-to-mr/crash_agent.py
```

### 3. Pipeline trigger token
Create a pipeline trigger token for the target repo (Settings > CI/CD >
Pipeline triggers) — this is the `GITLAB_TRIGGER_TOKEN` the Firebase function
sends.

### 4. Deploy the Firebase function

```
cd firebase-functions
./setup.sh
```

This prompts for your Firebase project ID, `GITLAB_HOST` (defaults to
`https://gitlab.com` — override for self-hosted GitLab), `GITLAB_PROJECT_ID`
(the target project's numeric ID), and `GITLAB_REF` (the branch to trigger
against — check your project's actual default branch, it isn't always
`main`). It writes those into `.env` (not committed), sets the
`GITLAB_TRIGGER_TOKEN` secret via a hidden Firebase CLI prompt (never written
to a file by this script), and offers to deploy immediately.

Prefer to do it by hand instead? The four values it's asking for are the
`defineSecret`/`defineString` params declared at the top of
`firebase-functions/index.js` — set them as Firebase function params/secrets
however you like, then:
```
firebase deploy --only functions:onCrashlyticsFatalIssue --project <your-firebase-project>
```

## How the agent decides what to change

The agent (`crash_agent.py`) is a tool-use loop, not a single prompt: Claude
is given `list_files` / `read_file` / `write_file` tools scoped to the
checked-out repository, investigates until it can point at a concrete root
cause, then calls `finish` with a report and (if confident) the new file
contents. The script commits those via the GitLab Commits API and opens the
MR — it never runs arbitrary shell commands and never touches
`.gitlab-ci.yml` or its own skill files (see `SKILL.md` for the full
constraints). If the agent isn't confident in a fix, it still reports its
findings but skips opening an MR. Retrying a failed CI job re-triggers the
script with the same `CI_PIPELINE_ID` (and so the same branch name); it
detects an already-existing branch or MR from a prior attempt and reuses it
instead of failing.

## Development

```
pip install -r templates/.agents/skills/crash-to-mr/requirements.txt pytest
pytest
```

`tests/test_crash_agent.py` covers the pure logic — path-traversal guarding,
file listing/reading, and commit-action building — without calling the
Anthropic or GitLab APIs.

## Known limitations

- Firebase Crashlytics alert events carry issue metadata (title, subtitle,
  app version) but not always a full stack trace — for richer context, wire
  up the Crashlytics BigQuery export and enrich the payload in
  `firebase-functions/index.js` before it's sent to GitLab.
- `crash_agent.py` defaults to `ANTHROPIC_MODEL=claude-3-5-sonnet-20240620`;
  update that default (or set the env var in CI) to whatever current Claude
  model you want the agent running on.
- Every fix is a first draft — review the MR before merging.
