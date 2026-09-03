# Sound cues go here

This folder is empty on purpose. No show audio is included in this
repository; the original Big Brother sound design belongs to CBS and is not
redistributable (see the top-level DISCLAIMER.md).

To add your own:

1. Run `python3 make_sounds.py` from `pi/` for a starter set of synthesized
   placeholder cues (spin, bell, reveal). No recordings needed, standard
   library only.
2. Replace any of them with your own recordings using the same filenames,
   as WAV or OGG.
3. Optionally record a line per prize and set that prize's `"sound"` field
   in `config.json`.

A missing file is skipped and logged at startup; the show runs silent until
cues exist.
