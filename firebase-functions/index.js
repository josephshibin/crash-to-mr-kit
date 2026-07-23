const { onNewFatalIssuePublished } = require("firebase-functions/v2/alerts/crashlytics");
const { defineSecret, defineString } = require("firebase-functions/params");
const logger = require("firebase-functions/logger");

const gitlabTriggerToken = defineSecret("GITLAB_TRIGGER_TOKEN");
const gitlabHost = defineString("GITLAB_HOST", { default: "https://gitlab.com" });
const gitlabProjectId = defineString("GITLAB_PROJECT_ID");
const gitlabRef = defineString("GITLAB_REF", { default: "main" });

// NOTE: verify this field mapping against the `firebase-functions` version you
// deploy with — the Crashlytics alert payload shape has changed across SDK
// versions. Also note the alert event does not include a full stack trace;
// for that, wire up the Crashlytics BigQuery export and enrich crashPayload
// below before sending it on.
exports.onCrashlyticsFatalIssue = onNewFatalIssuePublished(
  { secrets: [gitlabTriggerToken] },
  async (event) => {
    const issue = event.data?.payload?.issue ?? {};

    const crashPayload = {
      issueId: issue.id,
      title: issue.title,
      subtitle: issue.subtitle,
      appVersion: issue.appVersion,
      appId: event.appId,
      receivedAt: event.time,
    };

    const url = `${gitlabHost.value()}/api/v4/projects/${encodeURIComponent(gitlabProjectId.value())}/trigger/pipeline`;

    const body = new URLSearchParams();
    body.set("token", gitlabTriggerToken.value());
    body.set("ref", gitlabRef.value());
    body.set("variables[CRASH_PAYLOAD]", JSON.stringify(crashPayload));

    const response = await fetch(url, { method: "POST", body });

    if (!response.ok) {
      const text = await response.text();
      logger.error("Failed to trigger GitLab pipeline", { status: response.status, text });
      throw new Error(`GitLab trigger failed: ${response.status}`);
    }

    const result = await response.json();
    logger.info("Triggered GitLab pipeline", { pipelineId: result.id, webUrl: result.web_url });
  }
);
