# Nikon D5500 camera setup

Research checked 13 October 2026. No physical D5500 was available for validation.
This application imports completed photo batches from an SD card or local folder.
It does not issue camera commands, identify a connected camera, trigger a shutter,
or provide live view.

- Nikon's [compatibility table](https://www.nikonimgsupport.com/eu/BV_article?articleNo=000050786&configured=1&lang=pt_PT) lists D5500 USB support in Camera Control Pro 2, now discontinued.
- Nikon's [availability notice](https://www.nikonimgsupport.com/eu/BV_article?articleNo=000067053&configured=1&lang=en_GB) says Camera Control Pro 2 is no longer available for purchase or download in Europe. Its old Download Center link now redirects. Ask Nikon about your region; do not assume a new licence/download is available.
- [NX Tether 2.5.0](https://downloadcenter.nikonimglib.com/en/download/sw/276.html) does not list the D5500. It is not a supported replacement for this camera.
- The official [D5500 download page](https://downloadcenter.nikonimglib.com/en/products/196/D5500.html) offers NX Studio, including Nikon Transfer 2. File transfer is not a promise of immediate tethered shutter capture.
- [Camera Control Pro 2 storage documentation](https://nikonimglib.com/ccp2/onlinehelp/en/08_camera_controls_03.html) includes D5500 settings. An existing legitimately licensed installation may be usable, but has not been tested here with this camera or current Windows.

No Nikon software, SDK, licence key or third-party camera utility is bundled.

## SD-card workflow

1. Configure separate Incoming, Originals, Processed, and Export folders under **Assign**. Defaults are under Local AppData.
2. Photograph a class, remove the SD card safely, and insert it into the laptop.
3. Create or choose a batch, then choose **Import card folder**. JPEG, CR2, CR3, and NEF files are copied into managed Incoming storage before the card is removed.
4. Select one or more photographs on the left and the matching student on the right, then assign them.
5. Confirm the assignments to archive immutable originals. Resolve any import errors with **Retry**.
6. Open Review to process all confirmed photographs, inspect each result, edit its mask, and adjust portrait size and position.

## Required event rehearsal, not performed here

Use the actual D5500, SD card, reader, and PC. Verify JPEG and NEF import, large bursts,
duplicates, card removal after import, assignment across classes, processing, and export.
Record firmware/software versions, counts, and results before prize-giving. This build
makes no hardware-tested claim for shutter, live view, or automatic USB transfer.
