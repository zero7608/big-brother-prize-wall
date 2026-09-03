# Prize photos go here

This folder is empty on purpose. No prize photos, no show artwork, and no
Big Brother branding is included in this repository (see the top-level
DISCLAIMER.md for why).

To add your own:

1. Take or find a photo for each prize in your `config.json`.
2. Save it as PNG or JPG in this folder, roughly 1000px on the long edge.
   4:3 crops look best; anything else is auto-cropped to fit.
3. Set that prize's `"image"` field in `config.json` to the filename, e.g.
   `"image": "spa.png"`.

Until a file exists, that prize shows a plain colored box with its name
underneath, so the wall runs fine before any art is in place.
