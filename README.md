# Support Triage Agent

A small agent for API and webhook support tickets that asks for the identifiers it needs, checks the logs, and either resolves with evidence or escalates to engineering.

**Result:** In the offline demo it resolves two of three synthetic scenarios and escalates the third with a typed handoff.

**Status:** Prototype; offline mock demo only.

- Each step is one decision, one validated tool call and one state update. The agent can't use an ID nobody supplied, and it can't resolve a ticket without evidence.
- Every result comes from a rule-based stand-in for the model. No live-model results are included.

[Technical details →](docs/GUIDE.md)
