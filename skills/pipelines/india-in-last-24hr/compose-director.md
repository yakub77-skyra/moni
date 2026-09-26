# compose-director (india-in-last-24hr)

Render via `video_compose` on the india-last-24hr-map playbook: dark map,
highlighted state + rank badge, neon connector, dashed card, white
headline bar with outlet chip. The video ends on the last story card;
there is no end card. 720x1280, 30fps, h264+aac.

Compose must route by `edit_decisions.render_runtime` (no silent default).
If the selected runtime is HyperFrames and unavailable, surface a blocker and
request approval before switching runtime.

Verify with ffprobe (duration ±1s, resolution exact), sample one frame
per card (headline legible over footage, outlet chip visible, never text
over black), confirm music is mixed, confirm caption.txt lists all 5
article URLs for the post. Write render_report.
