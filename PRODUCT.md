# Student Headshot Manager

## Register

product

## Users and purpose

School photography workstation operators maintain class rosters and assign batches of headshots after a photo session.
The application provides persistent rosters, validated CSV imports with mapped delivery filenames, JPEG and Canon/Nikon RAW ingestion with explicit student/session assignment, local green-screen processing and mask/layout review, and explicit class/project exports.

## Design principles

- Follow the requested neutral charcoal-and-grey palette and compact workstation layout.
- Make student identity, class membership, and photography status easy to scan.
- Support persistent class ordering and explicit bulk roster maintenance.
- Provide large controls, keyboard access, readable text, and explicit validation.
- Keep direct camera control outside this stage.
- Prepare export reports before copying, preserve originals and never overwrite deliveries.
- Process photographs locally and require explicit human review of results.
- Copy card imports into managed storage, preserve originals, and show assignments before accepting them.

## Accessibility

Use native Qt controls, visible focus, textual status labels, labelled fields, and keyboard shortcuts. Avoid animation.
