# Nikon D5500 camera setup

Research checked 13 October 2026. No physical D5500 was available for validation.
This application monitors a local folder. It does not issue camera commands,
identify a connected camera, trigger a shutter or provide live view. Watching means
the folder is accessible, not that the camera USB connection is healthy.

- Nikon's [compatibility table](https://www.nikonimgsupport.com/eu/BV_article?articleNo=000050786&configured=1&lang=pt_PT) lists D5500 USB support in Camera Control Pro 2, now discontinued.
- Nikon's [availability notice](https://www.nikonimgsupport.com/eu/BV_article?articleNo=000067053&configured=1&lang=en_GB) says Camera Control Pro 2 is no longer available for purchase or download in Europe. Its old Download Center link now redirects. Ask Nikon about your region; do not assume a new licence/download is available.
- [NX Tether 2.5.0](https://downloadcenter.nikonimglib.com/en/download/sw/276.html) does not list the D5500. It is not a supported replacement for this camera.
- The official [D5500 download page](https://downloadcenter.nikonimglib.com/en/products/196/D5500.html) offers NX Studio, including Nikon Transfer 2. File transfer is not a promise of immediate tethered shutter capture.
- [Camera Control Pro 2 storage documentation](https://nikonimglib.com/ccp2/onlinehelp/en/08_camera_controls_03.html) includes D5500 settings. An existing legitimately licensed installation may be usable, but has not been tested here with this camera or current Windows.

No Nikon software, SDK, licence key or third-party camera utility is bundled.

## Folder workflow

1. Configure Incoming, Originals, Processed and Export as separate local folders in Capture. Defaults are under your Local AppData.
2. If you already have compatible licensed tethering software, configure its JPEG save destination to Incoming. Connect the camera directly with a suitable USB data cable. Check camera errors in that external software.
3. Otherwise transfer JPEGs into Incoming using Nikon Transfer 2 (separately available through NX Studio on the official D5500 page above), or copy from a card reader. Transfer may require explicit operator action. Continuous automatic delivery is unverified.
4. Select the student and capture session, then Resume watcher. Student selection does not advance automatically.
5. Confirm each displayed assignment. Delayed files use the student selected at first detection, so always check the proposed identity and counts.
6. Resolve transfer errors and use Retry/Resume. Incoming files and earlier takes remain unchanged.

## Required event rehearsal, not performed here

Use the actual D5500, cable, PC and transfer software. Verify JPEG delivery and delays,
bursts, reconnects, partial files, duplicates, battery/power loss and assignment
across student changes. Separately test external shutter triggering/live view if
used. Record firmware/software versions, counts and results before prize-giving.
This build makes no hardware-tested claim for shutter, live view or automatic USB transfer.
