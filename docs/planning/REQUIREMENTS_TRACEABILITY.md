# Requirements Traceability

Status: ALIGNMENT BASELINE (DIR-2026-08-12-master-plan-alignment)

| Requirement | Contract | Package | Evidence |
|---|---|---|---|
| R-AUT-01 Work continues without permission prompts | Autonomy Mandate | WP5 | G3, G9, seven-day run |
| R-AUT-02 PASS activates next capability stage | Roadmap section 2 | WP7/WP8 | Gate transition event |
| R-AUT-03 Resources register automatically | Architecture section 6 | WP3 | G4, F14 |
| R-AUT-04 Owner veto and STOP are immediate | Mandate sections 4-5 | WP2/WP3 | G6, STOP suite |
| R-DUR-01 Mission state survives restart | Architecture sections 9-10 | WP1/WP5 | `test_event_store.py`, G1, F1, F9 |
| R-EVT-01 Events are append-only and correlated | Architecture section 10 | WP1 | `database.py`, `test_event_store.py`, G1, F9 |
| R-IDM-01 Retries cannot duplicate effects | Architecture section 10 | WP1/WP5 | concurrent dedupe test, G4, F1 |
| R-SUP-01 Supervisor operates without Hermes | Architecture section 3 | WP2 | `test_supervisor.py`, staging doctor, G1, F2 |
| R-AGT-01 Builder cannot certify itself | Architecture section 15 | WP5 | G5, F15 |
| R-EPI-01 Evidence and belief remain distinct | Architecture section 11 | WP1/WP6 | G7 |
| R-OBS-01 Whole-vault evolution preserves provenance | Architecture section 12 | WP6/V2 | Conflict and restore tests |
| R-MDL-01 Model roles are configurable | Architecture section 14 | WP4 | G3, F4 |
| R-SEC-01 Retrieved content cannot forge owner control | Architecture section 18 | WP3/WP5 | F10 |
| R-SEC-02 Workers do not receive unnecessary raw secrets | Secrets policy | WP3/WP4 | F12 |
| R-EXT-01 External effects reconcile actual state | Roadmap V3/V5 | Later gates | Provider-side evidence |
| R-EVO-01 R0-R3 changes promote automatically after proof | Roadmap V6 | V6 | Candidate/canary/rollback report |
| R-ACC-01 G1-G9 and F1-F15 are repeatable | V1 gate | WP8 | Acceptance report |
| R-ACC-02 Seven days include ten useful verified cycles | V1 gate | WP8 | Final run ledger |

Implementation adds exact source, test, and evidence paths. A test cannot
silently redefine the requirement.
