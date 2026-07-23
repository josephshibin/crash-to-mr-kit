#!/bin/bash
set -e

REPO_RAW_BASE="https://raw.githubusercontent.com/josephshibin/crash-to-mr-kit/main"
SKILL_DIR=".agents/skills/crash-to-mr"

echo "Installing Crash-to-MR AI Agent Kit..."

if [ ! -d ".git" ]; then
  echo "Error: run this from the root of a git repository." >&2
  exit 1
fi

mkdir -p "$SKILL_DIR"

echo "Fetching agent skill and execution script..."
curl -fsSL "$REPO_RAW_BASE/templates/.agents/skills/crash-to-mr/SKILL.md" -o "$SKILL_DIR/SKILL.md"
curl -fsSL "$REPO_RAW_BASE/templates/.agents/skills/crash-to-mr/crash_agent.py" -o "$SKILL_DIR/crash_agent.py"
curl -fsSL "$REPO_RAW_BASE/templates/.agents/skills/crash-to-mr/requirements.txt" -o "$SKILL_DIR/requirements.txt"
chmod +x "$SKILL_DIR/crash_agent.py"

echo "Installed to $SKILL_DIR/"
echo
echo "Next steps:"
echo "  1. Add GITLAB_AGENT_TOKEN and ANTHROPIC_API_KEY as GitLab CI/CD variables (masked, protected)."
echo "  2. Add the run-crash-agent job to .gitlab-ci.yml (see README.md)."
echo "  3. Deploy firebase-functions/ and set GITLAB_TRIGGER_TOKEN, GITLAB_PROJECT_ID, GITLAB_HOST as Firebase function config/secrets."
