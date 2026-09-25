# script-director (india-in-last-24hr)

Call `openrouter_scriptwriter` with the brief's 5 stories
(model default: free tier). Demand exactly 5 cards back.

Validate before writing the script artifact:
- Card count == story count, order preserved (rank/state/outlet/URL intact).
- Each narration is 12-18 spoken words, neutral, no invented facts.
- No card copies its RSS headline verbatim — rewrite required.
- Each card has a `visual_query` for stock retrieval.

Write a schema-valid `script` (version 1.0, sections with id/text/start/end).
Approval gate: human reads all 5 narration lines before assets stage.
