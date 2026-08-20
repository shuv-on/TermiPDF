# Publishing TermiPDF to the Snap Store

A complete, copy-pasteable walkthrough from a fresh machine to your
snap being live on `snapcraft.io/termipdf`. Covers every rejection
reason I've seen in the snapcraft-review queue and how to avoid it.

**Audience:** the TermiPDF maintainer (you). Skip to a section by
`Ctrl+F` for what you need.

---

## Table of contents

1. [Pre-flight checklist](#1-pre-flight-checklist)
2. [One-time setup](#2-one-time-setup)
3. [Build the snap](#3-build-the-snap)
4. [Upload to the Snap Store](#4-upload-to-the-snap-store)
5. [What happens next (review queue)](#5-what-happens-next-review-queue)
6. [Common rejection reasons + fixes](#6-common-rejection-reasons--fixes)
7. [Promoting edge → candidate → stable](#7-promoting-edge--candidate--stable)
8. [Updates](#8-updates)
9. [Removing the snap](#9-removing-the-snap)

---

## 1. Pre-flight checklist

Run these on the project root **before** you build anything. If any
fail, fix them and re-run.

```bash
cd /path/to/TermiPDF

# 1. YAML valid + every reviewer-visible field present
python3 -c "
import yaml
m = yaml.safe_load(open('snap/snapcraft.yaml'))
required = ['name', 'base', 'version', 'summary', 'description',
            'grade', 'confinement', 'contact', 'issues',
            'source-code', 'website', 'icon', 'apps', 'parts']
missing = [k for k in required if k not in m]
assert not missing, f'missing: {missing}'
assert m['icon'].endswith('.png'), 'icon must be a .png'
assert 5 <= len(m['summary']) <= 78, f'summary length {len(m[\"summary\"])} (5-78)'
print('YAML OK')
"

# 2. Icon is square, >=256x256, PNG
python3 -c "
from PIL import Image
im = Image.open('snap/gui/termipdf.png')
assert im.size[0] == im.size[1] >= 256, f'bad size: {im.size}'
assert im.format == 'PNG', f'bad format: {im.format}'
print('icon OK', im.size)
"

# 3. .desktop file validates
desktop-file-validate snap/gui/termipdf.desktop && echo 'desktop OK'

# 4. License file present + readable
test -f LICENSE && echo 'LICENSE OK'

# 5. README has a clear project description
head -20 README.md | grep -q -i 'pdf' && echo 'README OK'

# 6. Smoke test still passes
python tests/smoke_test.py 2>&1 | tail -1
```

If any line reports an error, fix it before continuing. The
`contact:` field MUST be a real-looking email — `example.com` /
`example.invalid` addresses **will be auto-rejected**.

---

## 2. One-time setup

### 2a. Create a Snapcraft account

Go to **https://dashboard.snapcraft.io** and sign in with your
GitHub account (`shuv-on`). Snapcraft lets you publish under that
identity.

### 2b. Register the snap name

Snap names are **first-come, first-served globally**. Pick one of:

| Name | Visibility | Conflict risk |
|---|---|---|
| `termipdf` | Global, anyone can use | High — someone else may have it |
| `shuv-on-termipdf` | Global | Lower |
| `shuv-on/termipdf` | **Scoped to your account** (preferred) | None |

On the dashboard, click **Register new snap** and register
`termipdf`. The system will tell you if it's already taken. If it
is, fall back to the scoped form `shuv-on/termipdf` — that namespace
is reserved for your account.

### 2c. Install snapcraft + LXD on your build machine

```bash
sudo snap install snapcraft --classic
sudo snap install lxd
sudo lxd init --auto
snapcraft login
```

`snapcraft login` opens a browser to register a one-time token. After
this, every `snapcraft upload` knows who you are.

> **Why LXD?** Snapcraft builds snaps inside an Ubuntu container
> (so the build environment is reproducible across hosts). LXD is
> the simplest backend. If you can't install LXD, use
> `snapcraft remote-build` to push the build to Snapcraft's farm —
> see §3c.

### 2d. Generate a GPG key (optional but recommended)

Snapcraft auto-signs snaps with your account key, so a local GPG key
isn't strictly required. But if you later want to publish **source
packages** (`dput` to a PPA) or sign commits, set one up now:

```bash
gpg --full-generate-key --pinentry-mode loopback
# Email MUST match your snapcraft account
```

---

## 3. Build the snap

### 3a. Local build (with LXD)

```bash
cd /path/to/TermiPDF
snapcraft
```

Output: `termipdf_0.1.0_amd64.snap` in the project root.

First build takes ~10 minutes (downloads core22 + snaps all the
system libs). Subsequent builds reuse the cache and are ~30 s.

### 3b. Local lint (catches ~70% of reviewer complaints)

```bash
snapcraft lint
```

Common `lint` findings and their fixes:

| Output | Fix |
|---|---|
| `icon-not-square` | Make sure icon is exactly square |
| `icon-must-be-at-least-256x256` | Provide a 256×256 PNG |
| `desktop-file-invalid-key` | Run `desktop-file-validate snap/gui/termipdf.desktop` |
| `missing-contact-information` | Add `contact:` to snapcraft.yaml |
| `missing-issues-information` | Add `issues:` to snapcraft.yaml |
| `summary-not-markdown` | Strip backticks/asterisks from `summary:` |
| `metadata-mismatch` | `name`/`version` mismatch between snapcraft.yaml and the changelog |

### 3c. Remote build (no LXD)

```bash
snapcraft remote-build
```

Snapcraft uploads the source tree (NOT the snap itself) to a
hosted build farm, builds for amd64 + arm64 + armhf in parallel,
then gives you download links. Takes 10–20 min for the first
build.

### 3d. Verify the built snap

```bash
# File listing
unsquashfs -l termipdf_0.1.0_amd64.snap | head -40

# Sanity checks
test -f $(unsquashfs -d /tmp/termipdf-test termipdf_0.1.0_amd64.snap && \
    echo /tmp/termipdf-test/usr/bin/termipdf-wrapper)
test -f /tmp/termipdf-test/usr/share/applications/termipdf.desktop
test -f /tmp/termipdf-test/usr/share/icons/hicolor/256x256/apps/termipdf.png

# Local install + smoke test (DO THIS before uploading)
sudo snap install ./termipdf_0.1.0_amd64.snap --dangerous
termipdf                          # window opens
sudo snap remove termipdf
```

If the local smoke test fails, do NOT upload — fix and rebuild.

---

## 4. Upload to the Snap Store

### 4a. Direct upload (most common)

```bash
snapcraft upload --release=edge termipdf_0.1.0_amd64.snap
```

What happens:

1. Snapcraft pushes the `.snap` to `dashboard.snapcraft.io`.
2. It runs **automated review checks** (security scan, metadata
   validator, icon validator). These run in seconds.
3. If automated checks pass, the snap is **published to the
   `edge` channel** and a human reviewer is assigned.
4. You get an email when the review is done.

### 4b. Choose a primary channel

After upload, the snap is available on `edge`. To enable `stable` /
`candidate` / `beta`, do it from the dashboard:
**https://dashboard.snapcraft.io/snaps/termipdf** → **Channels** →
tick the boxes.

### 4c. Multi-arch (arm64, etc.)

If you only built `amd64.snap`, the snap will install on x86_64
Ubuntu only. To support arm64 (Raspberry Pi, Apple Silicon laptops
via Asahi, AWS Graviton), build for it too:

```bash
# Local — requires launching LXD on an arm64 image
sudo lxc launch ubuntu:22.04 arm64-builder -c architecture=aarch64
# ... (complex; easier:)

# Remote build gives you all arches automatically
snapcraft remote-build --build-on=amd64,arm64
```

Then upload each architecture separately:

```bash
snapcraft upload --release=edge termipdf_0.1.0_amd64.snap
snapcraft upload --release=edge termipdf_0.1.0_arm64.snap
```

---

## 5. What happens next (review queue)

After upload, the snap enters the **manual review queue**. The
reviewer is a human at Canonical who checks:

1. **App works as described** — they install the snap in a VM and
   try it.
2. **Strict confinement justification** — every plug you claim
   must actually be used. Unused plugs will be rejected.
3. **No malicious behaviour** — the snap doesn't exfiltrate data,
   doesn't write outside its home, doesn't try to escalate privs.
4. **Metadata matches reality** — description matches what the
   app does, icon isn't a stock image, contact email works.
5. **License** — the snap must ship a license file. We ship MIT.

**Typical review time:** 1–5 business days for a new snap. Updates
to an already-approved snap: hours.

You'll get an email with one of three outcomes:

- ✅ **Approved** — snap is now on the channel you uploaded to.
- ❌ **Rejected** — email contains the exact rejection reason and
  a link to the reviewer's notes.
- � **Needs more info** — reviewer asks a question; you reply
  through the dashboard.

If rejected, fix and re-upload:

```bash
# Fix the issue (see §6 for common ones)
# Bump the version
sed -i 's/version: "0.1.0"/version: "0.1.1"/' snap/snapcraft.yaml
# Rebuild
snapcraft
# Re-upload (snap-store requires new version for each upload)
snapcraft upload --release=edge termipdf_0.1.1_amd64.snap
```

---

## 6. Common rejection reasons + fixes

This is the section you actually need when something goes wrong.

### 6.1. **"App is not functional" / "App crashes on launch"**

**Cause:** the snap can't start because a system lib is missing,
or the launcher path is wrong.

**Fix:**

```bash
# Run the snap locally with debug env to see the actual error
sudo snap install ./termipdf_0.1.0_amd64.snap --dangerous
snap run --shell termipdf -c /bin/bash
# Inside the sandbox, check what's there:
ls /snap/termipdf/current/usr/bin/
which python3
python3 --version
# Look for missing libs:
ldd /snap/termipdf/current/usr/bin/termipdf-wrapper | grep 'not found'
exit
```

Common causes:
- Forgot to add a `stage-package` (e.g. `libgl1`).
- The launcher has a path that doesn't exist inside the snap.
- `PYTHONPATH` set wrong (must be `$SNAP/usr/lib/python3/dist-packages`).

### 6.2. **"Icon is a placeholder / icon is too small"**

**Cause:** icon is a generic download icon, or is smaller than
256×256, or isn't square.

**Fix:**
- Provide a real 256×256 (or larger) PNG that looks like your app.
- Our `snap/gui/termipdf.png` is generated to be exactly 256×256.
- If you swap the icon, also keep the SVG (`snap/gui/termipdf.svg`)
  so the design is editable.

### 6.3. **"Undeclared / unneeded plugs"**

**Cause:** snap declares plugs it doesn't use (reviewers check
this aggressively — every plug is a privilege).

**Fix:** declare only what you actually need. For TermiPDF we use:
- `home` — read/write PDFs in $HOME
- `removable-media` — PDFs on USB sticks
- `desktop`, `desktop-legacy`, `x11`, `wayland`, `opengl` — Qt6 GUI

If you later need `network` for a feature, add it AND mention it
in the description ("uses network for [purpose]").

### 6.4. **"License is missing or not OSI-approved"**

**Cause:** no LICENSE file, or the license is custom and non-OSI.

**Fix:** TermiPDF ships MIT (OSI-approved). Keep the LICENSE file
in the repo root. Snapcraft will detect it automatically.

### 6.5. **"Maintainer email is not a real address"**

**Cause:** `contact: me@example.com` or similar placeholder.

**Fix:** use a real, monitored email. GitHub's
`<username>@users.noreply.github.com` works without exposing your
address. Real Gmail/Outlook/etc. is also fine.

### 6.6. **"Description is too short / too long"**

**Cause:** reviewers want at least 5–10 lines explaining what the
app does.

**Fix:** our `snap/snapcraft.yaml` description has 22 lines. That's
the sweet spot.

### 6.7. **"Snap claims to use `classic` confinement without
permission"**

**Cause:** `confinement: classic` requires special approval.

**Fix:** we use `strict` confinement, which is auto-approved. Stay
on strict unless you genuinely need raw filesystem access.

### 6.8. **"Snap uses `sudo` / `setuid` binaries"**

**Cause:** security check; snaps must not escalate privileges.

**Fix:** TermiPDF uses none. If a future dep needs `sudo`, switch
to a different lib or use `confinement: devmode` (only for testing).

### 6.9. **"Snap contains pre-compiled binaries without source"**

**Cause:** reviewers want to be able to verify what runs.

**Fix:** TermiPDF pulls PyQt6 etc. via the `python` plugin from
PyPI, which counts as providing source. No action needed.

### 6.10. **"Snap is a fork of another snap and doesn't add value"**

**Cause:** if your snap looks like a reskin of an existing snap,
it'll be rejected.

**Fix:** TermiPDF is a fresh app, not a fork. The README and
description make this clear.

---

## 7. Promoting edge → candidate → stable

Once the snap is approved on `edge` and you've tested it:

```bash
# List your snap's revisions
snapcraft list-revisions termipdf

# Promote a specific revision
snapcraft release termipdf <revision> candidate
snapcraft release termipdf <revision> stable
```

Recommended schedule:

1. Day 0: upload to `edge`.
2. Day 1–2: smoke-test on `edge`. Install with
   `sudo snap install termipdf --edge`.
3. Day 3: if no complaints, promote to `candidate`.
4. Day 7: if `candidate` looks good, promote to `stable`.

You can also do it from the dashboard GUI.

---

## 8. Updates

For every new release:

```bash
# 1. Bump version in snap/snapcraft.yaml
sed -i 's/version: "0.1.0"/version: "0.1.1"/' snap/snapcraft.yaml

# 2. Add a changelog entry
dch -i    # edits debian/changelog — yes, snapcraft reads it for version info
# OR manually edit snap/local/VERSION + snap/snapcraft.yaml

# 3. Rebuild
snapcraft

# 4. Upload
snapcraft upload --release=edge termipdf_0.1.1_amd64.snap

# 5. Test on edge, then promote
snapcraft release termipdf <rev> candidate
snapcraft release termipdf <rev> stable
```

Snap auto-updates every 6 hours by default. Users on `stable` get
your update within hours of promotion.

---

## 9. Removing the snap

If you ever need to take it down:

```bash
# Close all channels
snapcraft close termipdf stable candidate beta edge

# Or via the dashboard: Settings → "Close all channels"
```

Once closed, the snap disappears from `snap find` within 6 hours.
Existing installs stay installed but won't get further updates.

To delete the snap entirely (rare; e.g. you're renaming the project):

- Go to dashboard → Settings → "Delete snap".
- This is irreversible; users get a "snap no longer available"
  error on next refresh.

---

## TL;DR (if you just want the commands)

```bash
# One-time
sudo snap install snapcraft --classic
sudo snap install lxd && sudo lxd init --auto
snapcraft login

# Every release
cd /path/to/TermiPDF
sed -i 's/version: "0.1.0"/version: "0.2.0"/' snap/snapcraft.yaml
snapcraft
snapcraft upload --release=edge termipdf_0.2.0_amd64.snap

# Wait for review approval email

snapcraft release termipdf <revision> candidate
snapcraft release termipdf <revision> stable
```

Users then install with:

```bash
sudo snap install termipdf
```
