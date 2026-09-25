# asset-director (india-in-last-24hr)

Per script section: `piper_tts` narration (offline default voice), one
licensed visual per `visual_query` via `pexels_video`/`pixabay_video`
(stills via Unsplash path if video unavailable), one music bed via
`pixabay_music` (fallback `freesound_music`).

Every asset_manifest entry needs provenance {provider, license, URL}.
Reject any publisher lead image that slipped in — regenerate from stock.
Approval gate: human listens to narration samples + views thumbnails.
