# Release status

## DONE

- Fixed demo iteration, schema lookup, escalation dispatch, and identifier-provenance defects.
- Verified all three mock scenarios with credentials/provider variables removed: two resolve and one escalates.
- Verified the visible decision, tool call, observation, state update, next decision, and stop trace.
- Added a presenter-friendly Streamlit console tailored to Technical Support Engineer — Triage & Discovery behaviours.
- Added guided authentication, webhook-boundary, and investigation-ready engineering-handoff walkthroughs.
- Added direct active-case scenario switching, previous/next navigation, clean re-runs, and completed-trace replay controls.
- Ran the complete suite, including UI interaction paths: **57 passed**.
- Re-tested install, dependency health, demo, and suite in a fresh copied checkout and virtual environment.
- Verified the Streamlit landing, clarification, same-case resume, resolution, reset, escalation, and handoff-download paths.
- Visually checked the interface at desktop and mobile sizes, including dark-theme contrast and active-case switching, with no browser exception or horizontal overflow.
- Replaced the machine-specific long README with a concise, portable runbook.

## CURRENT BLOCKER

- None.

## NEXT ACTION

- Use `python -m streamlit run streamlit_app.py` for the visual interview demonstration; keep `python demo.py` as the transparent command-line fallback.

## RISKS

- OpenAI mode was checked only at the adapter/setup surface; no credential-backed API call was made or required.
- Diagnostics are synthetic and state is in memory; production integrations are intentionally out of scope.
- This local project folder has no verified Git remote or published release.

## RELEASE STATUS

- **GO** — the required local, credential-free interview release is verified.
