# Release status

## DONE

- Fixed demo iteration, schema lookup, escalation dispatch, and identifier-provenance defects.
- Verified all three mock scenarios with credentials/provider variables removed: two resolve and one escalates.
- Verified the visible decision, tool call, observation, state update, next decision, and stop trace.
- Rebuilt the Streamlit surface as a hands-on Technical Support Engineer shift simulator.
- Added a live queue covering authentication, permissions, rate limiting, webhook, idempotency, precondition and internal-server cases.
- Added timed auto-arrivals, priority/SLA state, customer mood, support score and completed-case history.
- Added customer chat and exactly five shuffled support actions at discovery, diagnosis and response stages.
- Added consequences and coaching for unsafe, generic, premature and evidence-bounded decisions.
- Added optional OpenAI-generated incident wording and model-generated customer reactions using structured Responses API output.
- Constrained every generated interaction to the existing synthetic fixture truth; the model cannot create operational evidence.
- Ran the complete suite, including UI interaction paths: **66 passed**.
- Re-tested install, dependency health, demo, and suite in a fresh copied checkout and virtual environment.
- Verified queue intake, five-choice discovery, customer evidence reveal, diagnosis, score updates, completion and return-to-queue paths.
- Live browser verification confirmed the rebuilt queue, customer conversation, five answer controls and discovery-to-diagnosis transition.
- Replaced the machine-specific long README with a concise, portable runbook.

## CURRENT BLOCKER

- None.

## NEXT ACTION

- Use `python -m streamlit run streamlit_app.py --server.port 8503` for the visual interview demonstration; keep `python demo.py` as the transparent autonomous-agent fallback.

## RISKS

- The current OpenAI SDK is installed and the structured-output integration is wired, but no credential-backed API call was made because no key was handled by Codex.
- Diagnostics are synthetic and state is in memory; production integrations are intentionally out of scope.
- The public GitHub repository is `retinapeg/support-triage-agent`; `main` tracks `origin/main`.

## RELEASE STATUS

- **GO / PUBLISHED** — the credential-free interview build is verified and the public GitHub repository is live.
