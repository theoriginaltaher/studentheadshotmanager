# Student Headshot Manager

A native Windows desktop roster application for school photography workstations.
Built with Python 3.12, PySide6, SQLite, watchdog, OpenCV, NumPy, and Pillow. No browser or web server is involved.

## Install on Windows (no development tools required)

Run **StudentHeadshotManager-Setup.exe** from the release folder. Choose the default
installation under Program Files; Windows requests administrator permission. An
explicit per-user installation is also available without elevation. Start Menu
entry is included and a desktop shortcut is optional. Start **Student Headshot Manager**.
Python, pip, Git, Qt, OpenCV and Pillow do not need to be installed separately.
The installer is offline and never downloads packages at first launch.

Binary target: 64-bit Windows 10 1809 or later / Windows 11. Use a Windows edition
still receiving Microsoft support. See TEST_REPORT.md for the actual tested
platform and unperformed clean-machine checks. The installer is unsigned because
no publisher certificate was supplied; Windows may show an unknown-publisher prompt.

An optional **StudentHeadshotManager-Portable.zip** contains the same self-contained
application. Extract the whole ZIP to a writable location and run the EXE inside
StudentHeadshotManager; do not move the EXE away from its _internal directory.
Portable mode still uses per-user AppData for the live database and defaults.

The database, backups and logs live under `%LOCALAPPDATA%\StudentHeadshotManager`.
Initial photo folders are under its `Photos` directory and can be changed in Assign.
No live data is placed under Program Files. Upgrades retain existing data and folder
settings. Uninstall preserves all user records, backups and photographs.

For the Nikon D5500 card workflow and current software availability, read
[CAMERA_SETUP.md](CAMERA_SETUP.md). Physical-camera shutter, live view, and automatic
USB transfer are outside the application.

## Development and repeatable builds

Build operators only: see [packaging/README.md](packaging/README.md) and
`build_installer.ps1`. Source execution still supports Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## Roster

- Create, rename, and delete classes. Deleting a class requires confirmation and
  also deletes its students. Cancel is the default; deletion cannot be undone.
- Arrange classes in a saved custom order with the up/down controls. Ctrl/Shift-select
  multiple classes to move them as a group or delete them together.
- Create, edit, move, and delete students with a student ID, full name, class, and export filename.
- Ctrl/Shift-select multiple student rows to delete them together. Editing remains
  available only when one student is selected.
- Search by name, ID, or export filename; filter by class and photography status; and sort columns.
- Manually record **Not photographed**, **Captured**, **Needs review**, or **Complete**
  in the student editor. A status is a roster label, not proof that a photo exists.
- Import CSV files through column mapping and a row-by-row validation preview.
- Changes save immediately to SQLite and survive restarting the application.

Assign imports JPEG and Canon/Nikon RAW (`.cr2`, `.cr3`, `.nef`) batches from an SD card or local folder with explicit assignment review.
Review provides local green-screen removal and portrait compositing. The Export manager supports class and project deliveries. Direct camera
control remains unimplemented. Archived originals are
byte-for-byte copies.

## Import and assign a photo batch

1. Open **Assign** and choose **Configure storage**. Select separate Incoming,
   Originals, Processed, and Export folders. None may contain another.
2. Create or choose a batch for the class or shooting period.
3. Insert the SD card and choose **Import card folder**, or choose **Import photos**
   for selected files. JPEG, CR2, CR3, and NEF files are copied into managed Incoming
   storage before the card is removed.
4. Select one or more photographs on the left. Search or filter the roster on the
   right, select the student shown, and choose **Assign selected**.
5. Inspect the thumbnails and assignments, then choose **Confirm selected assignments**
   to archive immutable originals. Use **Discard** or **Retry** when appropriate.

The photograph list can be searched by filename, student name, or student ID. Once
assigned, photographs can be filtered by class and sorted by capture order, filename,
student, or class. Search, class, and assignment-status filters can be combined.

The photo list shows the selected batch and can be filtered by assignment state.
Duplicate deliveries are shown explicitly but excluded from received counts. Confirming a photograph
changes Not photographed to Captured; later/manual roster statuses are preserved.
Discarding/reassigning does not reset the manually editable roster status.

### Transfer stability and recovery

- watchdog notifications feed a background worker. A two-second rescan catches
  missed notifications. Hashing, full image decode, thumbnails, and copying run
  off the GUI thread using a separate SQLite connection.
- Files must keep the same size and modification time for at least two seconds
  and pass full pixel decoding. Stable but incomplete or
  unreadable files are retried internally for 30 seconds, then display an error
  with **Retry import**. Continuously growing files stay Receiving.
- A durable Receiving record includes the proposed student and session immediately.
  Partial data produces no thumbnail or accepted original. Capture time is the
  source modification time in UTC, explicitly labelled, not inferred EXIF time.
  Images are limited to 250 MB. Decoded working images are limited to 40 megapixels.
- Pause stops new detections; already detected files may finish. Confirmation,
  reassignment, discard, and retry remain available. Resume also scans existing files.
  Unseen files left during a pause are proposed for the student/session selected
  on resume, so review their assignments carefully.
- SHA-256 identity is globally unique across sessions and filenames, including
  after restart. Duplicate deliveries never create a second accepted original.
  Reused filenames with new content can be received as new photographs. Failed
  incomplete files require an explicit Retry.
- Confirmation checks the source against the preview hash. If the source changed,
  acceptance fails instead of copying a different photograph. Review the newly
  detected version rather than treating the old preview as the new image.
- Copy failures retain the same record and are retryable. Opening Capture after
  restart marks interrupted transfers/copies as failed. Retry recognizes an already
  completed hash-verified copy without creating another. Monitoring starts paused.
- A per-database process lock allows one capture worker at a time. Other instances
  report the conflict. Closing the window finishes in-flight file work asynchronously.

### Original storage and history

```text
Originals/YYYY-MM-DD/<session-id>/student-<safe-student-id>-<record-id>/<capture-id>-<random-id>.<source-ext>
```

Unique IDs and exclusive creation prevent filename collisions from overwriting
files. Incoming files are only opened for reading. Copies are hashed and flushed
before reporting Accepted. Failed copies remove only files created by that attempt.
A hard process/power interruption may leave a `.part` or incomplete output file;
retry uses a fresh destination unless the recorded copy is already valid.

Reassigning an accepted photo preserves the archived file at its original location;
SQLite's current assignment is authoritative. Discarding never deletes Incoming or
archived originals. Identity snapshots and assignment event history survive roster
student/class deletion. Folder settings, sessions, thumbnails, assignments, and
errors persist in SQLite. Version-one, version-two, and version-three databases are upgraded additively to version
four without rebuilding roster tables or losing capture records.

## Student and take review

Open **Review → Students & takes** to browse by class and student. The thumbnail
grid shows all assigned takes across sessions, excluding duplicate deliveries and
discarded assignments. Unconfirmed or failed imports retain their visible capture
status; confirm their assignment in Capture before full preview or final selection.

Choose a take to switch between its original, transparent cutout and grey composite.
Missing or invalid processed outputs are clearly identified; **Adjust processing**
opens the existing processing tools for that take. **Open full resolution** loads
native-resolution images locally in a separate zoomable window. Ordinary previews
are limited to 2048 pixels; output files remain unchanged.

**Mark selected** explicitly chooses one final take for the student and replaces any
previous choice. Browsing, processing and processing approval never choose a final
take automatically. **Clear selection** removes the choice. Final take selection is
independent of processing approval and does not certify that processing is complete.

Choose **Retake required**, **Processing adjustment**, or **Manual review**, then
**Save flag** to flag a take. A flag clears that take's final selection and prevents
reselection until the operator chooses **No flag / resolved**. Resolving a flag
never selects it automatically. These take flags are separate from detailed mask
review notes in Processing tools. Reassignment or discard also clears final selection.
Selections and flags persist in SQLite across restarts.

Filters: **All**, **Not photographed** (no active takes), **Needs review** (takes
without a final selection, or flagged takes), **Selected** (a final choice exists),
and **Retake required** (at least one take needs a retake). Filters can overlap:
a student can have a selected take and another take requiring attention. Student
navigation follows the currently filtered list and stops at either end.

Review-browser shortcuts: **Alt+Left** previous student, **Alt+Right** next student,
**Ctrl+Return** mark selected, **Ctrl+O** open full resolution. Double-clicking a take
also opens full resolution. These shortcuts apply only inside Students & takes.

## Local green-screen processing

1. Confirm assignments in **Assign**, then open **Review → Processing tools**.
2. Click **Even green screen preset** for a starting point, or adjust the settings.
3. Choose **Process all assigned** for the batch, or **Process locally** for one photograph.
   Processing runs in a background queue using the archived
   original. No images are uploaded and no cloud service or downloaded model is used.
4. Inspect **Results** and **Edit mask**. Paint with Keep or Remove at full mask
   resolution, then apply the edits to rebuild the cutout and portrait.
5. In **Position and size**, drag the portrait to move it and drag its lower-right
   handle to resize it. Reprocess after layout changes.
6. Set the background with RGB, Hex, or HSL values. The default delivery is a
   1080 × 1080 square JPEG at 300 DPI.
7. **Approve result** only after inspection, or **Flag manual review** with a note.
   Changing settings clears stale result previews until processing runs again.
   **Open output folder** opens the saved local files.

HSV thresholds use OpenCV hue units 0–179 and saturation/brightness units 0–255.
Noise removal opens the foreground mask; hole cleanup closes it. Radii are measured
in source pixels. Negative edge shift erodes the subject; positive shift dilates it.
Feathering applies Gaussian smoothing. Spill strength and edge-band width control
suppression of excess green near subject edges, leaving the interior unaffected.
The preset is a starting point for evenly lit green screens, not a quality guarantee.

EXIF orientation is applied to the full-resolution source pixels. The transparent
RGBA PNG and grayscale mask retain those upright dimensions. The portrait uniformly
fits the full frame inside the chosen canvas and padding, then applies the selected
scale and position over the chosen background color. It does not stretch the subject.
PNG/JPEG output orientation is
normalized to 1. ICC profiles are retained; other original EXIF metadata is not
copied in full. Original files remain unchanged. Processing explicitly rejects
sources over 250 MB or 40 million decoded pixels instead of silently reducing resolution.

```text
Processed/<capture-id>/<cache-prefix>-<job-id>/cutout.png
Processed/<capture-id>/<cache-prefix>-<job-id>/mask.png
Processed/<capture-id>/<cache-prefix>-<job-id>/portrait.jpg
```

JPEG defaults to quality 95 with no chroma subsampling. RAW files are developed locally
through rawpy/LibRaw for previews and processed outputs; archived RAW originals remain
unchanged for later color/exposure edits. Each job records its settings,
source hash, output paths/hashes, status, diagnostics, and errors in SQLite. The cache
key includes source content, every processing setting, and pipeline version. Valid
cached files are reused across restarts; missing or changed outputs are regenerated.
Changing the Processed folder affects new outputs; existing cache paths stay valid.
Storage failures and interrupted jobs remain visible and can be retried with
**Process locally**. Closing the app waits for in-flight work without blocking the GUI.

Every new result requires explicit operator approval. Heuristics detect empty or
almost-full foregrounds, disconnected regions, holes, and other suspicious masks.
Severe separation failures block approval; other warnings require acknowledgment.
These checks can miss poor edges, hair detail, shadows, and green clothing. Manual
flags persist across reprocessing and clear only through explicit approval. Photo
approval does not automatically set the student's roster status to Complete.

### Synthetic processing samples

Generate four geometric test images into a new folder (existing files are not overwritten):

```powershell
.\.venv\Scripts\python.exe samples\generate_processing_samples.py "D:\SyntheticPhotos"
```

Samples cover an evenly green backdrop, missing green, empty green, and a green
patch inside the subject. Automated tests validate mechanics and failure handling;
they do not establish processing quality on real student photographs or performance
on a particular workstation. Inspect representative local photographs before use.

## Import a roster

1. Choose **Import CSV** (Ctrl+I), then select a UTF-8 comma-separated CSV.
2. Map columns to **Student ID**, **Full name**, **Class**, and **Filename**. Common headers are
   mapped automatically; any other column names can be mapped manually.
3. Inspect the preview and validation messages. Hover a truncated cell to read
   its full content. Use **Show only errors** to inspect rejected rows.
4. Correct errors in the source CSV and reopen it. Import is enabled only when
   every row is valid. New classes are created automatically.

Try [samples/students.csv](samples/students.csv), which contains 12 fictional
students in four classes. Importing that file twice is intentionally rejected.

Student IDs are globally unique across all classes and are always stored as
text, preserving leading zeros present in the CSV. Duplicate comparison trims
whitespace, normalizes Unicode, and ignores case. Class names use the same
comparison rules. Filenames are also globally unique without regard to case. Names
are not unique: two students may share a full name. Student IDs allow 80 characters;
names, class names, and filenames allow 200. All four fields are required; control
characters and Windows-invalid filename characters are rejected. Extra CSV columns are ignored
after mapping, but rows must match the header's column count. Blank data rows are
validation errors. Header names must be non-empty and unique.

The preview's CSV row number counts logical CSV records, including the header,
not physical lines inside quoted fields. Limits: 10 MB and 20,000 data rows.
CSV import is synchronous; a large preview may briefly occupy the application.
All import rows are revalidated at commit time and written in one transaction.
Existing records are never silently overwritten or skipped; any error blocks
the whole import. Cancelling the preview makes no changes.

## Export manager

1. Open **Export** and choose a class or **Entire project**.
2. Choose selected grey portraits, selected transparent PNGs, all originals,
   all processed takes, complete class, or complete project. Complete class requires
   a class; complete project requires Entire project. Complete exports contain all
   available originals, transparent PNGs and grey JPEGs in the chosen scope.
3. Optionally check **Also include every take** to add all three kinds of file,
   regardless of the primary export type. Otherwise selected-portrait modes export
   only the explicitly chosen take for each student.
4. Explicitly choose whether to **Include incomplete students**. This is off by
   default. A student is incomplete if they have no selection, their selected
   processing is not approved, or relevant files are missing/invalid. Including
   them copies available files and labels their state; it never invents a selection
   or substitutes another take for a missing selected portrait.
5. Choose a destination and click **Prepare export**. Review missing selections,
   errors, student counts and file counts before clicking **Start export**.
6. Watch progress and review the completion summary. **Open exported folder** opens
   the resulting local folder. Copy errors are listed and retained in the reports.

Every export creates a new `Export-<timestamp>-<unique-id>` directory. Existing
exports are never overwritten, so no overwrite confirmation is needed. Processed
filenames use the roster's mapped **Filename** value with `-TR.png` for transparent
images and `-GR.jpg` for grey-background images. Original files retain the student
ID and student name naming convention. If a folder would otherwise contain duplicate
names, the later files receive ` (2)`, ` (3)` and so on. Complete names remain in the manifest.
Class folders retain a readable name and internal class ID:

```text
Export-<timestamp>-<unique-id>/
  Class name--<class-id>/
    Originals/<student-id> - <name>.<source-ext>
    Transparent/<filename>-TR.png
    Grey Backgrounds/<filename>-GR.jpg
  manifest.csv
  files.csv
  summary.json
```

Only folders needed by the selected options are created. `manifest.csv` has one row
per scoped student, including excluded students and students without photographs.
It contains student ID, name, class, mapped export filename, selected filename(s), processing status, inclusion,
exported filenames and errors. Selected filenames only reference successful copies.
`files.csv` lists each successful copy, its take, selection flag, processing status,
review flag and SHA-256 hash. `summary.json` records counts and copy errors.
CSV files use UTF-8 with BOM; import student IDs as text to preserve leading zeros.

Planning and copying run in a background worker. SQLite is opened read-only with
no migrations or updates. Source files are read and hash-verified, and each new copy
is verified. If the project changes after preparation, prepare again. Once copying
starts, the export represents the prepared snapshot; subsequent operator changes
apply to the next export. Changed or inaccessible files produce errors rather than
silent substitutions. A failed export may leave a partial delivery; review its
reports and rerun into a new folder after fixing the issue. Close waits for the
current export to finish. Exports cannot be written inside protected photo storage.

All-original and complete exports include archived discarded takes for included
students, and project-wide exports retain archived photographs whose
roster entry was deleted under **Unassigned (deleted roster)**. Their old class is
not recoverable from the existing capture schema. Unconfirmed Incoming files are
not copied; confirm and archive them in Capture first. Processed exports use each
take's latest valid recorded job, not historical cache versions or mask intermediates.
A complete project export is a photograph delivery, not a SQLite project backup.

## Storage and backup

Default database location on Windows:

```text
%LOCALAPPDATA%\StudentHeadshotManager\roster.sqlite3
```

This location is independent of the working directory. The application creates
the directory and schema automatically. Hover **Local SQLite storage** in the
status bar to see the actual path. To use a different file:

```powershell
.\.venv\Scripts\python.exe app.py --database "D:\School Rosters\roster.sqlite3"
```

Use **Data & help → Back up database** for a consistent SQLite snapshot, including
committed WAL data. Startup creates a checked snapshot once per UTC day and before
upgrading an older schema, in the database's adjacent `Backups` directory. Backups
are not automatically deleted. Watch available disk space and archive older copies.

Close every application instance before manually copying the database for backup. Back up
Originals and Processed too; full photographs are not embedded in SQLite. SQLite
uses WAL mode; copying only the main file while the application is open can miss
recent changes. Store the database on a local disk. The database is not encrypted.
The schema uses foreign keys, unique normalized identity keys, status constraints,
and a version number for future migrations. Tests use temporary databases.

## Recovery and troubleshooting

Use **Data & help → Open logs** to find `application.log` (rotating, five retained
2 MB files) and `crash.log`. Logs contain local paths/error details; no images are
uploaded. Startup never silently resets an unreadable/corrupt roster. Check disk
space and permissions first. Only one running application may own a given roster.

To restore a known-good database backup, close the application and run:

```powershell
& 'C:\Program Files\Student Headshot Manager\StudentHeadshotManager.exe' --restore-backup 'D:\Backups\roster-backup.sqlite3'
```

The application asks for confirmation, validates SQLite integrity/schema, preserves
the current valid database as another snapshot (or preserves damaged bytes separately),
then restores the snapshot. Use `--database 'D:\Roster\roster.sqlite3'` as well to
restore a non-default roster. This command is part of the bundled app, not Python.
Photo files are never moved by database restore; restore their backups to their
recorded paths separately. Renamed photo folders require fixing paths/configuration.

After interruption, Capture and Review recover unfinished jobs as retryable errors.
Resume the watcher or click Retry import / Process locally after resolving the cause.
Exports may leave a partial delivery with error reports; rerun into a new folder.
For missing DLL/Qt plugin errors, reinstall or re-extract the complete distribution;
do not download DLLs from third-party sites. A denied Program Files install requires
administrator approval or the explicit per-user installer option. If AppData is not
writable, restore permissions/free space before launching. No network access is
required for local operation; optional camera-software acquisition is separate.

For diagnostic support, run the installed EXE with `--self-test` and a NEW output
folder. It uses synthetic data only and reports JSON results; see packaging/README.md.
This diagnostic is not a substitute for an actual event-camera rehearsal.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+Shift+N | New class |
| Ctrl+N | Add student |
| Ctrl+I | Import CSV |
| Ctrl+F | Search students |
| F2 | Edit selected student |
| Ctrl+Delete | Delete selected student, with confirmation |
| F5 | Refresh roster |
| Alt+1 / 2 / 3 / 4 | Classes / Capture / Review / Export |
| Ctrl+Q | Quit |
| Tab / Shift+Tab | Move between controls |
| Escape | Cancel a dialog |

Double-click a student to edit. Shortcuts are also listed in the Roster menu.
Filters remain active after edits and imports; clear them if a student is hidden.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests cover schema initialization, persistence across reconnection, class and
student CRUD, status changes, filtering, uniqueness, foreign keys, cascade
deletion, import mapping and validation, duplicate detection, rollback, and Qt
editor/roster/import interactions. Capture tests cover incomplete/growing images,
duplicates across restarts, snapshot assignment, reassignment, discard, copy
collisions, source changes, retry, interrupted-copy recovery, schema migrations, class
ordering and bulk roster deletion, a real
watchdog observer, pause/resume, worker ownership, thumbnails, and capture controls.
Processing tests cover all eight EXIF orientations, HSV and cleanup controls, alpha
and aspect ratio, spill suppression, cache invalidation, output integrity, retries,
manual review, migration, GUI responsiveness, and stale-result protection. Review
tests cover final-selection persistence and replacement, flags, filters, assignment
invalidation, keyboard navigation, and full-resolution image dimensions. Export
tests cover mapped filenames, safe filenames, duplicate names, class structure, missing selections,
source changes, read-only database behavior, manifests and background UI flow.
Qt tests use the offscreen platform and do not
require a camera. They do not replace testing with a physical workstation's
display scaling, keyboard, and assistive technology.

## Code layout

```text
app.py                              Small launcher
student_headshot_manager/
  app.py                            Startup and storage path
  database.py                       Schema, transactions, roster CRUD
  roster_import.py                  CSV reading, mapping, validation, import
  capture_repository.py             Additive schema, sessions, assignments, history
  folders.py                        Folder configuration and access checks
  ingestion.py                      Settling, JPEG/RAW verification and safe copying
  capture_worker.py                 Background worker and watchdog lifecycle
  exporting.py                      Read-only export plans, manifests and safe copies
  export_worker.py                  Background planning and export progress
  review_repository.py              Final-take selections and review flags
  processing.py                     Local masks, spill suppression and compositing
  processing_repository.py          Processing schema, history and review state
  processing_service.py             Cache, safe output writes and approval checks
  processing_worker.py              Background processing queue and previews
  ui/
    main_window.py                  Sidebar, roster, search and actions
    dialogs.py                      Class and student editors
    import_dialog.py                CSV preview and mapping UI
    capture_page.py                 Thumbnails, active student and assignment actions
    capture_dialogs.py              Folder configuration and reassignment
    export_page.py                   Export options, preflight report and summary
    review_workspace.py             Student navigation, takes grid and full previews
    review_page.py                  Before/after, mask and operator review
    processing_settings.py          Adjustable processing settings and preset
    image_view.py                   Native image pan and zoom
    theme.py                        Charcoal-and-grey Qt theme
samples/students.csv                Fictional sample roster
samples/generate_processing_samples.py  Synthetic image generator
tests/                              Roster, ingestion, processing and Qt tests
```

Qt API reference: [Qt for Python documentation](https://doc.qt.io/qtforpython-6/).
