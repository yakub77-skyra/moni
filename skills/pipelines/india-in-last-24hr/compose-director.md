# compose-director (india-in-last-24hr)

Render via `video_compose` on the india-last-24hr-map playbook: dark map,
highlighted state + rank badge, neon connector, dashed card, white
subtitle bar, end card. 720x1280, 30fps, h264+aac.

Verify with ffprobe (duration ±1s, resolution exact), sample one frame
per card (subtitle legible over footage, never over black), confirm music
is mixed, confirm end card lists all 5 sources. Write render_report.
