# idea-director (india-in-last-24hr)

Call `india_news_scraper` with `{max_items: 5, lookback_hours: 24,
fetch_article_body: true}`. If fewer than 5 fresh stories come back, widen
to 48h once, then 72h — never fabricate stories.

Write the brief with `metadata.stories`: 5 entries of
{rank, outlet, headline, summary, source_url, state}. States must differ
across at least 3 cards (map variety). Record the visual plan per story:
stock query + map highlight state. Note explicitly that publisher lead
images are reference-only.

Before locking the production plan, Present Both composition runtimes for
`render_runtime` selection: Remotion and hyperframes. Recommend one for this
brief, but wait for explicit user approval and log the decision as
`render_runtime_selection`.

Approval gate: human confirms the 5 stories + states before script stage.
