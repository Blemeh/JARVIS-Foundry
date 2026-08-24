# Taskward Ownership Boundary — WP10

**Question**: Does Taskward belong to Lane-1 GitHub research?

**Answer**: No. Correct architecture after Post-4 integration:

- **Lane 1 (live-github-research)** — *observes / researches / discovers* repositories, issues, CI, docs. It produces `Opportunity` objects with duplicate keys and feeds the existing V7A portfolio. It never owns product implementation.

- **Taskward** — *belongs to Windows scheduler / release-continuity capability*. It solves the continuity backbone problem: Task Scheduler state that the Supervisor and worker-loop depend on. Evidence: `installer.py:139-422`, `V7_S2_MIGRATION_REHEARSAL F1`, `PROJECT_STATE` scheduler notes.

- **Canonical integration target** (future, not in this promotion): `Supervisor / release management → governed Task Scheduler operation → Taskward-style implementation`. This integration will reuse Taskward's export/validate/plan/apply contracts through the existing Supervisor/mission/effect path. It is recorded as a **future wake/integration target**, not scope-creep for this post-4 lane.

**Proof that production scheduler plumbing was NOT replaced**:

- `src/jarvis_foundry/platform/windows_subprocess.py` unchanged in this promotion (check `git diff --name-only` excludes scheduler plumbing except for Taskward's own isolated repo).
- `evidence/integration/post4-lineage/TASKWARD_LINEAGE.json` marks `production_scheduler_mutated: false`.
- Taskward lives under `C:/temp/JARVIS-Autonomous-Project-Lab/taskward` (isolated lab path) or `greenfield_projects/taskward` (disposable factory), not under `foundry` Scheduler live tables.
- No migration touches live DB; no Supervisor restart.

**Future wake conditions** (when scheduler integration becomes valuable):
- `taskward/MAINTENANCE.md` revisit `2026-09-24`
- Windows build changes scheduler cmdlet behavior
- drift false-positive > threshold
- owner requests scheduler automation handoff via `portfolio.work_registered` with kind `MAINTENANCE` or `IMPROVEMENT` targeting the scheduler capability
