#!/usr/bin/env python3
"""Perceive-Reason-Act loop: reads a Crashlytics payload, lets Claude
investigate the checked-out repo via tool use, and opens a GitLab MR
for any confident fix via the Commits API."""
import json
import os
import sys
from pathlib import Path

import anthropic
import gitlab

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20240620")
MAX_TURNS = 20
IGNORED_DIR_PARTS = {".git", "node_modules", ".agents", "vendor", "build", "dist"}

REQUIRED_ENV = [
    "ANTHROPIC_API_KEY",
    "GITLAB_AGENT_TOKEN",
    "CI_SERVER_URL",
    "CI_PROJECT_ID",
    "CI_PIPELINE_ID",
    "CRASH_PAYLOAD",
]

REPO_ROOT = Path(os.environ.get("CI_PROJECT_DIR", ".")).resolve()
CHANGED_FILES = {}

TOOLS = [
    {
        "name": "list_files",
        "description": "List source files under a directory in the repository, relative to repo root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list, relative to repo root. Defaults to root."}
            },
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file from the repository, relative to repo root.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Record the full new contents of a file, relative to repo root. Does not touch disk; the content is committed via the GitLab API once analysis finishes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "finish",
        "description": "Call exactly once when analysis is complete, whether or not a fix was made.",
        "input_schema": {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string"},
                "summary": {
                    "type": "string",
                    "description": "Markdown report: the issue, the root cause, and the changes made (or why none could be made confidently).",
                },
                "mr_title": {"type": "string"},
                "confident_fix": {
                    "type": "boolean",
                    "description": "True only if a concrete code change was made via write_file.",
                },
            },
            "required": ["root_cause", "summary", "mr_title", "confident_fix"],
        },
    },
]


def die(message):
    print(f"crash_agent: {message}", file=sys.stderr)
    sys.exit(1)


def load_env():
    missing = [key for key in REQUIRED_ENV if not os.environ.get(key)]
    if missing:
        die(f"missing required environment variables: {', '.join(missing)}")
    return {key: os.environ[key] for key in REQUIRED_ENV}


def load_skill_prompt():
    skill_path = Path(__file__).with_name("SKILL.md")
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return "You are an autonomous senior software engineer fixing a production crash."


def safe_path(rel_path):
    candidate = (REPO_ROOT / rel_path).resolve()
    if candidate != REPO_ROOT and REPO_ROOT not in candidate.parents:
        raise ValueError(f"path escapes repository root: {rel_path}")
    return candidate


def tool_list_files(rel_path="."):
    base = safe_path(rel_path)
    if not base.exists():
        return {"error": f"no such path: {rel_path}"}
    if base.is_file():
        return {"entries": [rel_path]}
    entries = []
    for path in sorted(base.rglob("*")):
        if any(part in IGNORED_DIR_PARTS for part in path.parts):
            continue
        if path.is_file():
            entries.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))
        if len(entries) >= 500:
            break
    return {"entries": entries}


def tool_read_file(rel_path):
    path = safe_path(rel_path)
    if not path.is_file():
        return {"error": f"no such file: {rel_path}"}
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"error": f"not a text file: {rel_path}"}
    return {"content": content[:20000]}


def tool_write_file(rel_path, content):
    path = safe_path(rel_path)
    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    CHANGED_FILES[rel] = {"content": content, "existed": path.exists()}
    return {"ok": True}


def dispatch_tool(name, tool_input):
    if name == "list_files":
        return tool_list_files(tool_input.get("path", "."))
    if name == "read_file":
        return tool_read_file(tool_input["path"])
    if name == "write_file":
        return tool_write_file(tool_input["path"], tool_input["content"])
    if name == "finish":
        return {"ok": True}
    return {"error": f"unknown tool: {name}"}


def run_agent_loop(client, crash_payload):
    system_prompt = load_skill_prompt()
    messages = [
        {
            "role": "user",
            "content": (
                "A new fatal crash was reported. Crashlytics payload:\n\n"
                f"```json\n{json.dumps(crash_payload, indent=2)}\n```\n\n"
                "Investigate the repository (you are at its root), locate the root cause, and if "
                "you can confidently fix it, write the corrected file(s) with write_file. Call "
                "finish exactly once when you are done."
            ),
        }
    ]

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = []
        finish_payload = None
        for block in tool_uses:
            result = dispatch_tool(block.name, block.input)
            if block.name == "finish":
                finish_payload = block.input
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)}
            )
        messages.append({"role": "user", "content": tool_results})

        if finish_payload is not None:
            return finish_payload

    return None


def branch_name_for(pipeline_id):
    return f"ai-fix/crash-{pipeline_id}"


def build_commit_actions(changed_files):
    return [
        {
            "action": "update" if meta["existed"] else "create",
            "file_path": path,
            "content": meta["content"],
        }
        for path, meta in changed_files.items()
    ]


def _is_conflict(exc):
    return "already exists" in str(exc).lower()


def open_merge_request(env, finish_payload, changed_files):
    if not changed_files:
        print("crash_agent: no files changed; skipping merge request.")
        return

    gl = gitlab.Gitlab(env["CI_SERVER_URL"], private_token=env["GITLAB_AGENT_TOKEN"])
    project = gl.projects.get(env["CI_PROJECT_ID"])
    default_branch = os.environ.get("CI_DEFAULT_BRANCH") or project.default_branch
    branch_name = branch_name_for(env["CI_PIPELINE_ID"])

    # A retried CI job reuses the same CI_PIPELINE_ID, so a prior attempt may
    # have already created this branch/MR. Treat that as success, not failure.
    try:
        project.branches.create({"branch": branch_name, "ref": default_branch})
    except gitlab.exceptions.GitlabCreateError as exc:
        if not _is_conflict(exc):
            raise
        print(f"crash_agent: branch {branch_name} already exists, reusing it.")

    project.commits.create(
        {
            "branch": branch_name,
            "commit_message": f"AI fix: {finish_payload['mr_title']}",
            "actions": build_commit_actions(changed_files),
        }
    )

    try:
        mr = project.mergerequests.create(
            {
                "source_branch": branch_name,
                "target_branch": default_branch,
                "title": f"[AI] {finish_payload['mr_title']}",
                "description": finish_payload["summary"],
                "remove_source_branch": True,
            }
        )
    except gitlab.exceptions.GitlabCreateError as exc:
        if not _is_conflict(exc):
            raise
        existing = project.mergerequests.list(source_branch=branch_name, state="opened")
        if not existing:
            raise
        mr = existing[0]
        print(f"crash_agent: merge request for {branch_name} already exists, reusing it.")

    print(f"crash_agent: opened merge request {mr.web_url}")


def main():
    env = load_env()
    try:
        crash_payload = json.loads(env["CRASH_PAYLOAD"])
    except json.JSONDecodeError as exc:
        die(f"CRASH_PAYLOAD is not valid JSON: {exc}")

    client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])
    finish_payload = run_agent_loop(client, crash_payload)

    if finish_payload is None:
        die(f"agent did not call finish within {MAX_TURNS} turns")

    print(finish_payload["summary"])
    open_merge_request(env, finish_payload, CHANGED_FILES)


if __name__ == "__main__":
    main()
