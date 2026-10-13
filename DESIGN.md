# Desktop interface

Operators prepare rosters at a school photography workstation, reading names
quickly and moving between keyboard entry and CSV imports. The requested dark
neutral palette reduces visual distraction around the roster.

- Native PySide6 widgets with Fusion styling; no HTML or browser renderer.
- Charcoal main surface (#252525), darker navigation (#1c1c1c), and grey controls.
- Light text (#eeeeee), secondary text (#bdbdbd), visible focus outlines.
- Segoe UI, 10-point body type; 25-pixel screen title and 17-pixel section titles.
- Fixed navigation sidebar, resizable class/roster split, expanding name column.
- 40-pixel roster rows; large buttons and inputs; compact form spacing.
- Text always identifies status. No color-only status meaning or motion.
- Qt stylesheets require supported color formats, so the implementation uses
  sRGB hex values rather than browser-only OKLCH syntax.

- Review pairs source and result previews with a scrollable settings panel.
- A dedicated full-resolution mask view supports fit, 100%, zoom and pan.
- Changed settings clear stale results; textual checks and persistent manual flags
  make uncertainty visible. Output approval always requires operator action.

- Student-first review uses a class/status filter, student list and takes grid.
- Final selection has an explicit text label distinct from the highlighted browse item.
- Original, cutout and composite tabs share a preview area; full-resolution viewing
  is available on demand. Processing controls remain in their own tab.

- Export uses a two-step workflow: inspect missing selections and inclusion choices,
  then start copying. A persistent report and progress bar show results and errors.
