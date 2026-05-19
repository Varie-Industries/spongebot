# Recording the README GIF

## Setup (one time)

1. Install Kap: `brew install --cask kap` (free, MIT-licensed, exports to optimized GIF)
2. Open iTerm2. Set window to **1100 x 700**, font **Menlo 14pt**, theme **Solarized Dark** or similar.
3. Hide the dock and menu bar for a clean frame.

## Capture

```bash
cd /Volumes/VARIE\ INDUSTRIES/bolt-application/spongebot-github-repo
python scripts/demo_session.py
```

1. Open Kap, click **Record**, drag the crosshair around the iTerm window.
2. Set FPS to **30**.
3. Press **Record**.
4. Switch to iTerm and run the demo script.
5. Wait for the "SpongeBot remembers" line to finish, then stop Kap.
6. Export as GIF, target **<3 MB** so GitHub renders it inline.
7. Save as `docs/demo.gif` in the repo.

## Add to README

After saving the GIF, drop this under the badge row in `README.md`:

```markdown
<p align="center">
  <img src="docs/demo.gif" alt="SpongeBot demo: absorb, recall, health" width="720"/>
</p>
```
