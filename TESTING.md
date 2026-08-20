# Testing TermiPDF

Four ways to test TermiPDF, from fastest to most thorough. Pick the
one that fits your situation.

| # | Scenario | Time | What you learn |
|---|----------|------|---------------|
| 1 | Interactive — same machine | ~1 min | "Does it launch + can I click around?" |
| 2 | Automated — same machine | ~30 s | "Are all 108 internal checks still green?" |
| 3 | Another device via .snap | ~10 min | "Does it run on a fresh Ubuntu install?" |
| 4 | Snap packaging itself | ~10 min | "Will `snap install termipdf` actually work?" |
| 5 | Build + install a `.deb` | ~5 min | "Will `sudo dpkg -i termipdf_*.deb` work?" |

---

## 1. Interactive — same machine

This is what you'll do most of the time during development. Open a
terminal on the desktop (not over SSH — the GUI won't appear):

```bash
cd /path/to/TermiPDF
source .venv/bin/activate
python src/main.py
```

A window opens. Things to try:

| Action | How |
|---|---|
| Open a PDF | `Ctrl+O`, pick a file |
| Page navigation | `PgUp` / `PgDn`, `Home` / `End`, or click the page-number box in the status bar |
| Zoom | `Ctrl++` / `Ctrl+-` / `Ctrl+0` for fit |
| Outline sidebar | `View → Outline` (or click the **Outline** button on the toolbar) |
| Thumbnail rail | `View → Thumbnails` (or click the **Thumbs** button) |
| Embedded terminal | `Ctrl+T` or `Ctrl+`` — type `help` to see all 35 commands |
| Switch theme | `Ctrl+Shift+T` |
| Fullscreen | `F11` |
| Annotate — draw | Pick the pen tool, drag across the page |
| Annotate — highlight | Pick the highlighter, drag over text |
| Add Unicode text | `Ctrl+T` → terminal → `addtext txt "আমার বাংলা" --page 1 --x 100 --y 200` |
| QR share | Terminal → `qr "https://example.com"` |
| Drag a PDF onto the window | Drop it on the canvas |
| Drag images onto the window | Multi-image drop → converted to a new PDF on the spot |
| Quit | `Ctrl+Q` |

If anything crashes, run with logging visible:

```bash
python -u src/main.py 2>&1 | tee /tmp/termipdf.log
```

---

## 2. Automated — same machine (headless)

Two scripts that don't need a display:

### 2a. Smoke test (108 checks)

```bash
cd /path/to/TermiPDF
.venv/bin/python tests/smoke_test.py
```

Expected last line:
```
ALL 108 CHECKS PASSED ✓
```

The smoke test covers: imports, all 35 commands registered, every
image→PDF edge case (3 sizes + missing files), tab management,
annotation undo/redo round-trips, QR encoding, drag-drop detection,
and theme switching. Runs in ~3 seconds.

### 2b. Xvfb screenshot test (verifies the real GUI works)

```bash
sudo apt install -y xvfb imagemagick   # one-time, if missing
xvfb-run -a .venv/bin/python tests/capture_real_app.py
```

This boots the real app on a virtual X display, drives it through
each feature (`open`, `toc`, `thumbs`, draw/highlight modes, `addtext`,
terminal + `help`, `qr`, theme flip, image→PDF), and writes 12 PNGs
to `docs/screenshots/`. Watch the stdout — every scenario that
succeeds prints:

```
  ✓ 01-viewer.png   -> docs/screenshots/01-viewer.png  (NN bytes)
  ✓ 02-toc.png      -> docs/screenshots/02-toc.png  (NN bytes)
  ...
```

If a PNG comes back suspiciously small (<10 KB) the widget failed to
paint — re-run with `xvfb-run -a -s "-screen 0 1280x800x24"` to force
a specific resolution and inspect the PNG manually.

---

## 3. Another device via .snap

Build the snap locally, transfer it, install it on the target.

### 3a. Build (one-time setup on a Linux box with snapcraft)

```bash
sudo snap install snapcraft --classic
sudo snap install lxd
lxd init --auto
snapcraft login         # sign in with your Snap Store account

cd /path/to/TermiPDF
snapcraft               # builds termipdf_0.1.0_amd64.snap (~5–10 min)
```

### 3b. Install on the target device

**Same machine, no transfer needed:**
```bash
sudo snap install ./termipdf_0.1.0_amd64.snap --dangerous
termipdf
```

**Different machine:**

```bash
# Source machine — copy the file
scp termipdf_0.1.0_amd64.snap user@target:/tmp/

# Target machine — install
sudo snap install /tmp/termipdf_0.1.0_amd64.snap --dangerous
termipdf
```

The `--dangerous` flag tells snapd to skip signature verification —
fine for local testing, NOT for distribution. For real users, push to
your Snap Store edge channel first:

```bash
snapcraft upload --release=edge termipdf_0.1.0_amd64.snap

# Then on the target:
sudo snap install termipdf --edge
```

### 3c. Verify confinement works

Once installed under strict confinement, exercise every feature that
needs a plug:

```bash
# Open a PDF from $HOME (needs `home` plug)
termipdf ~/Documents/sample.pdf

# Open a PDF from a USB stick (needs `removable-media`)
termipdf /media/$USER/USB Stick/sample.pdf

# Toggle theme (needs `desktop`)
# Press Ctrl+Shift+T in the running window

# Use the QR tool (needs `network` — actually unused today, but ok)
# Terminal → qr "https://example.com"
```

If you get **Permission denied** or files don't appear in file
dialogs, a plug is missing. Edit `snap/snapcraft.yaml`, add the plug
under `apps.termipdf.plugs`, rebuild.

---

## 4. Test the snap packaging itself

Two layers: **static lint** (fast, no LXD) and **full build** (slow).

### 4a. Static lint

```bash
snapcraft lint
```

Reports missing icons, broken `.desktop` entries, etc. **Must be clean
before uploading to the store.**

### 4b. Full build

```bash
snapcraft clean                      # optional: start from scratch
snapcraft                            # ~5–10 min the first time
ls -lh *.snap                        # should show termipdf_0.1.0_amd64.snap
```

Common build failures + fixes:

| Error | Cause | Fix |
|---|---|---|
| `cannot find libxcb-cursor0` | Wrong Ubuntu repo series | Drop the line from `stage-packages` |
| `ModuleNotFoundError: PyQt6` at runtime | `python-deps` part failed silently | Re-run with `snapcraft --debug` |
| `.desktop` file not validated | Missing `Icon=` line or wrong path | Fix `snap/gui/termipdf.desktop` |
| White window at runtime | Missing OpenGL libs | Add `libgl1`, `libegl1` to `stage-packages` |
| `Failed to load module colorreload-gtk-module` | Just a GTK warning under snapd | Safe to ignore |

### 4c. Inspect the built snap

```bash
# What's inside?
unsquashfs -l termipdf_0.1.0_amd64.snap | head -40

# Check the launcher is executable
unsquashfs -d /tmp/termipdf-test termipdf_0.1.0_amd64.snap
ls -l /tmp/termipdf-test/usr/bin/termipdf-wrapper
cat /tmp/termipdf-test/usr/bin/termipdf-wrapper
```

### 4d. Remote build (no local LXD)

If you can't install LXD, use Snapcraft's hosted build farm:

```bash
snapcraft remote-build
# Snapcraft uploads the source tree, builds on farm, downloads .snap
```

You get one `.snap` per supported architecture (currently `amd64`,
`arm64`, `armhf`).

---

## 5. Build + install a `.deb`

Useful when:
- You want to install on a machine without the Snap daemon.
- You're building for a private network / air-gapped environment.
- You want apt to resolve the Python deps for you.

### 5a. Build (one-time setup)

```bash
sudo apt install -y build-essential fakeroot debhelper devscripts lintian
cd /path/to/TermiPDF
dpkg-buildpackage -us -uc -b -nc
ls -lh ../termipdf_*.deb
```

Flags:
- `-us -uc` — don't sign source/changes (no GPG key needed for personal builds).
- `-b` — binary-only build (skips the `.dsc` source package).
- `-nc` — no clean (faster incremental rebuilds; drop for release builds).

Outputs land in the **parent** directory:
- `termipdf_0.1.0-1_amd64.deb` — the installable package
- `termipdf_0.1.0-1_amd64.buildinfo` — build metadata
- `termipdf_0.1.0-1_amd64.changes` — change log

### 5b. Lint

```bash
lintian --info --display-info ../termipdf_0.1.0-1_amd64.deb
```

Must have **no `E:` errors**. Warnings are tolerated for personal
builds; clean them up before publishing.

### 5c. Install

```bash
sudo dpkg -i termipdf_0.1.0-1_amd64.deb
sudo apt-get install -f        # fix any unmet deps if apt complains
termipdf                       # launches the installed app
```

`dpkg -i` will pull these from apt automatically if they're not yet
installed: `python3-pyqt6`, `python3-fitz`, `python3-qrcode`,
`python3-pil`, plus 20 Qt6 system libs (libxcb, libgl1, etc.).

### 5d. Verify the install

```bash
dpkg -l termipdf              # confirm installed
dpkg -L termipdf              # list every file the package owns
which termipdf                # /usr/bin/termipdf
man termipdf                  # man page from /usr/share/man/man1/
xdg-open /path/to/some.pdf    # opens in TermiPDF
```

### 5e. Uninstall

```bash
sudo apt remove termipdf
sudo apt autoremove           # pulls in any orphan Python deps
```

### 5f. Cross-device transfer

```bash
# Source — copy the .deb (and any cpu-arch-specific .debs)
scp ../termipdf_*.deb user@target:/tmp/

# Target (Ubuntu 22.04 / 24.04 / 25.04 with the same arch)
sudo apt update
sudo dpkg -i /tmp/termipdf_*.deb
sudo apt-get install -f
```

### 5g. Known build-deps to install

`dpkg-buildpackage` enforces `Build-Depends` in `debian/control`.
You need:

```bash
sudo apt install -y \
    build-essential fakeroot debhelper devscripts \
    dh-python python3-pyqt6 python3-all
```

If you already have `dpkg-buildpackage` working without these, the
`-d` flag skips the check (still builds, but doesn't guarantee a
clean build-root):

```bash
dpkg-buildpackage -us -uc -b -nc -d
```

### 5h. Common errors

| Error | Cause | Fix |
|---|---|---|
| `Unmet build dependencies: dh-python python3-pyqt6` | First-time build-host | `sudo apt install -y dh-python python3-pyqt6` |
| `dpkg-gencontrol: warning: Depends field ... ${python3:Depends} used` | `dh-python` not active in rules | Already covered by `dh $@` if `debhelper-compat` ≥ 12 |
| `package-installs-python-pycache-dir` (lintian E) | Stale `__pycache__/` in `src/` | `find src -name __pycache__ -prune -exec rm -rf {} +` then rebuild |
| `description-synopsis-starts-with-article` (lintian W) | Description starts with "A/An/The" | Rephrase: change "A modern PDF editor" to "Modern PDF editor" |
| `debian-changelog-has-wrong-day-of-week` (lintian W) | Date string doesn't match the calendar day | Use `date '+%a, %d %b %Y %H:%M:%S %z' -u` and copy into `debian/changelog` |
| `dpkg-deb: building package 'termipdf' in '../termipdf_*.deb' works but install fails with "dependency problems"` | Missing apt deps on the target | `sudo apt-get install -f` to fetch them |
| `termipdf: command not found` after install | `/usr/bin` not on PATH, or package didn't actually install | `echo $PATH` then `dpkg -L termipdf \| grep bin` |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ImportError: No module named PyQt6` | `pip install -r requirements.txt` inside the venv |
| Window opens then dies silently | Check `~/.config/TermiPDF/TermiPDF.conf` for bad settings; delete it to reset |
| `python src/main.py` says "No module named main_window" | You're not in the project root, or you skipped `source .venv/bin/activate` |
| Smoke test fails on `import fitz` | PyMuPDF wheel didn't build — `pip install --upgrade pymupdf` |
| Snap build fails with "build-packages not found" | Check `snap/snapcraft.yaml` — Ubuntu 22.04 (jammy) repos only |
| App icon is a grey rectangle under snap | `snap/gui/termipdf.png` is missing or wrong size (must be ≥256×256) |
| `termipdf` command not found after `snap install` | The snap needs `--classic` to add to PATH, OR open from the Activities overview |
| Theme toggle doesn't change anything | This was a known bug — fixed. Make sure you're on commit ≥ `89af743` |

---

## Pre-release checklist

Before tagging a release and pushing to `stable`:

- [ ] `python tests/smoke_test.py` → 108/108
- [ ] `xvfb-run -a python tests/capture_real_app.py` → 12 PNGs, all >20 KB
- [ ] Open `docs/screenshots/01-viewer.png` and eyeball it (sanity check)
- [ ] Interactive smoke test (Section 1) on your dev machine
- [ ] `snapcraft lint` → clean
- [ ] `snapcraft` → builds without warnings
- [ ] `sudo snap install ./termipdf_X.Y.Z_amd64.snap --dangerous` → opens
- [ ] `snapcraft upload --release=edge termipdf_X.Y.Z_amd64.snap`
- [ ] Install from edge on a fresh VM: `sudo snap install termipdf --edge`
- [ ] All Section 1 actions still work under strict confinement
- [ ] Bump `version:` in `snap/snapcraft.yaml` + `snap/local/VERSION`
- [ ] `snapcraft release termipdf <rev> stable`