#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Crash-to-MR: Firebase function setup"
echo

if [ ! -f package.json ]; then
  echo "Error: run this from the firebase-functions/ directory." >&2
  exit 1
fi

if ! command -v firebase >/dev/null 2>&1; then
  echo "Error: firebase CLI not found. Install it first: npm install -g firebase-tools" >&2
  exit 1
fi

read -rp "Firebase project ID (e.g. restoreme-prod): " FIREBASE_PROJECT
while [ -z "$FIREBASE_PROJECT" ]; do
  read -rp "Firebase project ID is required: " FIREBASE_PROJECT
done

read -rp "GitLab host [https://gitlab.com]: " GITLAB_HOST
GITLAB_HOST="${GITLAB_HOST:-https://gitlab.com}"

read -rp "GitLab project ID (numeric, shown on the project's main page): " GITLAB_PROJECT_ID
while [ -z "$GITLAB_PROJECT_ID" ]; do
  read -rp "GitLab project ID is required: " GITLAB_PROJECT_ID
done

read -rp "GitLab branch to trigger against [main]: " GITLAB_REF
GITLAB_REF="${GITLAB_REF:-main}"

cat > .env <<EOF
GITLAB_HOST=$GITLAB_HOST
GITLAB_PROJECT_ID=$GITLAB_PROJECT_ID
GITLAB_REF=$GITLAB_REF
EOF
echo
echo "Wrote .env (GITLAB_HOST, GITLAB_PROJECT_ID, GITLAB_REF)."

echo
echo "Next: set the GITLAB_TRIGGER_TOKEN secret. firebase will prompt for the"
echo "value with hidden input -- it is never written to a file by this script."
firebase functions:secrets:set GITLAB_TRIGGER_TOKEN --project "$FIREBASE_PROJECT"

echo
read -rp "Deploy onCrashlyticsFatalIssue to $FIREBASE_PROJECT now? [y/N] " CONFIRM
if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
  npm install
  firebase deploy --only functions:onCrashlyticsFatalIssue --project "$FIREBASE_PROJECT"
else
  echo "Skipped deploy. Run it yourself when ready:"
  echo "  firebase deploy --only functions:onCrashlyticsFatalIssue --project $FIREBASE_PROJECT"
fi
