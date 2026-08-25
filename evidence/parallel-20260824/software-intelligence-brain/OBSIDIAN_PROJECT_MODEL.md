# OBSIDIAN_PROJECT_MODEL (Phase K)

Mirror: disposable temp vault per test (make_service in
tests/foundry/obsidian_fixture.py). Owner live vault untouched.

Namespace: governed writer opt-in flag allow_projects enables exactly one
safe segment 'Projects/<name>.md' under the V1 write root; V1 note set,
Audit/ exclusivity, frontmatter contract (origin/current_steward/system/
version) unchanged for all pre-existing behavior (canonical suites green).

Project note shape (render_project_note): Identity (resource_id), Goal,
Current State, Decisions, Known Failures (labeled lessons NOT truth),
Lessons That Worked, Unknowns, Evidence Pointers; wikilinks between project
notes ([[Projects/x]]); short human-readable sections, not logs.

Protocol proofs (test_obsidian_mirror.py): update via expected-hash;
concurrent stale edit -> ObsidianConflictError with incoming bytes preserved
under Audit/conflicts/ and disk keeping the concurrent edit; rollback of a
COMPLETED write restores byte-exact before-state and refuses clobbering
newer edits; malformed frontmatter refused BEFORE any file/ledger mutation;
post-write semantic verification answers what/why-trusted/what-failed/
lesson/unknown questions from note content alone.
