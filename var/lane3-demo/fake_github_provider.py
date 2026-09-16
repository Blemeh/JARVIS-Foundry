"""Deterministic file-backed GitHub provider used only by the Lane 3 demo.

The store is a JSON document written atomically (temp file + os.replace) so a
crashed child process and a fresh recovery process observe the same provider
truth — exactly what the crash/recovery proof needs. It models provider
identity honestly: POST assigns monotonic ids; GET returns what was stored;
nothing is pre-baked as PASS.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

_ISSUE_COLLECTION = re.compile(r"^/repos/([^/]+)/([^/]+)/issues$")
_ISSUE_COMMENT_COLLECTION = re.compile(r"^/repos/([^/]+)/([^/]+)/issues/(\d+)/comments$")
_PULLS_COLLECTION = re.compile(r"^/repos/([^/]+)/([^/]+)/pulls$")


class FakeGitHubProvider:
    """One provider store shared across processes by file path."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store_path = self.root / "provider.json"
        if not self.store_path.exists():
            self._save(self._empty())

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "next_issue_number": 1,
            "next_comment_id": 1001,
            "next_pull_number": 1,
            "writes": 0,
            "issues": [],
            "comments": [],
            "pulls": [],
        }

    def _load(self) -> dict[str, Any]:
        document = json.loads(self.store_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise RuntimeError("fake provider store is corrupt")
        return document

    def _save(self, document: Mapping[str, Any]) -> None:
        temporary = self.store_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.store_path)

    def write(self, endpoint: str, payload: Mapping[str, Any]) -> tuple[int, bytes]:
        document = self._load()
        document["writes"] += 1
        issue_match = _ISSUE_COLLECTION.fullmatch(endpoint)
        if issue_match is not None:
            owner, repo = issue_match.groups()
            number = int(document["next_issue_number"])
            document["next_issue_number"] = number + 1
            issue = {
                "number": number,
                "title": str(payload.get("title", "")),
                "body": str(payload.get("body", "")),
                "html_url": f"https://github.com/{owner}/{repo}/issues/{number}",
            }
            document["issues"].append(issue)
            self._save(document)
            return 201, json.dumps(issue).encode("utf-8")
        comment_match = _ISSUE_COMMENT_COLLECTION.fullmatch(endpoint)
        if comment_match is not None:
            owner, repo, issue_number = comment_match.groups()
            comment_id = int(document["next_comment_id"])
            document["next_comment_id"] = comment_id + 1
            comment = {
                "id": comment_id,
                "body": str(payload.get("body", "")),
                "issue_url": (
                    f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
                ),
                "html_url": (
                    f"https://github.com/{owner}/{repo}/issues/{issue_number}"
                    f"#issuecomment-{comment_id}"
                ),
            }
            document["comments"].append(comment)
            self._save(document)
            return 201, json.dumps(comment).encode("utf-8")
        pull_match = _PULLS_COLLECTION.fullmatch(endpoint)
        if pull_match is not None:
            owner, repo = pull_match.groups()
            number = int(document["next_pull_number"])
            document["next_pull_number"] = number + 1
            pull = {
                "number": number,
                "title": str(payload.get("title", "")),
                "body": str(payload.get("body", "")),
                "head": {"ref": str(payload.get("head", ""))},
                "base": {"ref": str(payload.get("base", ""))},
                "html_url": f"https://github.com/{owner}/{repo}/pull/{number}",
            }
            document["pulls"].append(pull)
            self._save(document)
            return 201, json.dumps(pull).encode("utf-8")
        raise RuntimeError(f"fake provider does not serve write endpoint {endpoint!r}")

    def read(self, endpoint: str) -> tuple[int, bytes]:
        document = self._load()
        queryless = endpoint.split("?", 1)[0]
        if endpoint.endswith("?state=all&per_page=100") or endpoint.endswith("?per_page=100"):
            base = queryless
            if base.endswith("/pulls"):
                return 200, json.dumps(document["pulls"]).encode("utf-8")
            if re.fullmatch(r"/repos/[^/]+/[^/]+/issues", base):
                return 200, json.dumps(document["issues"]).encode("utf-8")
            if re.fullmatch(r"/repos/[^/]+/[^/]+/issues/\d+/comments", base):
                return 200, json.dumps(document["comments"]).encode("utf-8")
        item = re.fullmatch(r"/repos/[^/]+/[^/]+/issues/(\d+)$", queryless)
        if item is not None:
            number = int(item.group(1))
            for issue in document["issues"]:
                if issue["number"] == number:
                    return 200, json.dumps(issue).encode("utf-8")
        comment_item = re.fullmatch(r"/repos/[^/]+/[^/]+/issues/comments/(\d+)$", queryless)
        if comment_item is not None:
            comment_id = int(comment_item.group(1))
            for comment in document["comments"]:
                if comment["id"] == comment_id:
                    return 200, json.dumps(comment).encode("utf-8")
        pull_item = re.fullmatch(r"/repos/[^/]+/[^/]+/pulls/(\d+)$", queryless)
        if pull_item is not None:
            number = int(pull_item.group(1))
            for pull in document["pulls"]:
                if pull["number"] == number:
                    return 200, json.dumps(pull).encode("utf-8")
        return 404, b"{}"

    @property
    def write_calls(self) -> int:
        return int(self._load()["writes"])

    @property
    def issues(self) -> list[dict[str, Any]]:
        return list(self._load()["issues"])

    @property
    def pulls(self) -> list[dict[str, Any]]:
        return list(self._load()["pulls"])

    @property
    def comments(self) -> list[dict[str, Any]]:
        return list(self._load()["comments"])
