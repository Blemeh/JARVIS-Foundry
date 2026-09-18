"""One-shot: declare normalized input roles on builtin recipe parameters."""
from __future__ import annotations

import re
from pathlib import Path

PATH = Path(__file__).resolve().parents[4] / "src" / "jarvis_foundry" / "fabric" / "recipes" / "builtins.py"
TEXT = PATH.read_text(encoding="utf-8")

BINDS = {
    "v14.research_to_artifact": {"artifact_target": "artifact_target_relative_path"},
    "v14.open_and_verify": {
        "process_id": "native_process_id",
        "application": "native_application",
    },
    "v14.publish_verified_work": {
        "worktree": "owned_repository_root",
        "remote": "repository_remote",
        "branch": "repository_branch",
    },
    "v14.digital_operator.close": {
        "source_url": "authenticated_source_url",
        "site_origin": "browser_site_origin",
        "session_state_path": "browser_session_state_path",
        "artifact_target": "artifact_target_relative_path",
        "process_id": "native_process_id",
        "application": "native_application",
        "worktree": "owned_repository_root",
        "remote": "repository_remote",
        "branch": "repository_branch",
    },
    "v14.artifact_to_local_repo": {"worktree": "owned_repository_root"},
}

NEEDS = {
    "v14.research_to_artifact": ("web.research_read", "artifact.create_update"),
    "v14.open_and_verify": ("native.document_verify",),
    "v14.publish_verified_work": ("repository.publish",),
    "v14.digital_operator.close": (
        "web.authenticated_interaction", "artifact.create_update",
        "native.document_verify", "repository.preserve",
    ),
    "v14.research_to_docx": ("document.compose",),
    "v14.research_to_xlsx": ("spreadsheet.compose",),
    "v14.artifact_to_local_repo": ("artifact.stage_to_repository",),
    "v16.bounded_note": ("note.bounded_write",),
}

# split into recipe blocks by the recipe_id marker
markers = [(m.start(), m.group(1)) for m in re.finditer(r'"recipe_id": "([^"]+)",', TEXT)]
changed = 0
output = [TEXT[: markers[0][0]]]
last = 0
for index, (start, recipe_id) in enumerate(markers):
    end = markers[index + 1][0] if index + 1 < len(markers) else len(TEXT)
    block = TEXT[start:end]
    for param, role in BINDS.get(recipe_id, {}).items():
        pattern = re.compile(r'("%s": \{)' % re.escape(param))
        new_block, count = pattern.subn(r'\1\n                "bind": "%s",' % role, block)
        assert count == 1, f"{recipe_id}.{param}: {count} matches"
        block = new_block
        changed += 1
    needs = NEEDS.get(recipe_id)
    if needs:
        tag_line = re.search(
            r'^(\s*)"tags": \(.*\),(?P<eol>\r?\n)', block, flags=re.MULTILINE,
        )
        assert tag_line is not None, f"{recipe_id}: tags line not found"
        rendered = ", ".join(f'"{item}"' for item in needs)
        indent = tag_line.group(1)
        eol = tag_line.group("eol")
        addition = f'{indent}"needs": ({rendered}),{eol}'
        block = block[: tag_line.end()] + addition + block[tag_line.end():]
        changed += 1
    output.append(block)

PATH.write_text("".join(output), encoding="utf-8")
print(f"inserted {changed} bind roles")
