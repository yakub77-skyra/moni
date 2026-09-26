# asset-director (india-in-last-24hr)

Per script section: `piper_tts` narration (offline default voice), one
licensed visual per `visual_query` via `pexels_video`/`pixabay_video`
(stills via Unsplash path if video unavailable), one music bed via
`pixabay_music` (fallback `freesound_music`).

Every asset_manifest entry needs provenance {provider, license, URL}.
The card image is the outlet's own lead photo: record it as
{publisher-lead-image, credited, article_url}, render a visible
"Photo: <outlet>" credit on the card, and keep the article URL on the end
card. Stock is the fallback only, when the publisher image is unavailable.
Approval gate: human listens to narration samples + views thumbnails.
