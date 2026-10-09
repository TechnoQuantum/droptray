# Using DropTray from an iPhone

The iPhone and PC must be on the same network (the PC can be on Ethernet).
Run `setup-firewall.ps1` once on each PC so the phone can reach it.

## 1. The DropTray page (recommended, no setup)

1. In DropTray → **Phone** tab, scan the QR code with the iPhone camera.
2. The page pairs itself and moves to **`http://droptray.local:8765`**.
3. In Safari: **Share → Add to Home Screen**.

That one Home Screen icon works with **every PC you pair**: at home it opens the home PC,
at work the work PC. To pair another PC, open the icon on that PC's network and tap
**Ask the PC to pair**, then click **Accept** on the PC. No code needed.

The page has three tabs:
- **Send**: photos, videos and files (any number; progress, speed, Cancel), plus text and links.
- **From PC**: files and text the PC shared (drop files on the drawer, or **Send clipboard**).
- **More**: speed test, and the PCs this phone is paired with.

> Some office networks block Bonjour (`.local` names) or stop devices from seeing each
> other. In the first case the page stays on the PC's IP address and still works; in the
> second, no local-network app can work there.

## 2. Share sheet: "Send to PC"

Puts **Send to PC** in the Share sheet of Photos, Files, Safari and other apps.

1. **Shortcuts** → **+** → name it `Send to PC`.
2. **ⓘ** → turn on **Show in Share Sheet** (Images, Media, Files, URLs, PDFs).
3. Add **Repeat with Each** → *Shortcut Input*.
4. Inside it, **Get Contents of URL**:
   - URL `http://droptray.local:8765/upload`
   - Method **POST**
   - Headers: `X-Token` = your pairing code, `X-Device` = `iPhone`
   - Request Body **Form**: field type **File**, key `file`, value *Repeat Item*

Tip: in the drawer menu (`⋯`) → **Set pairing code…** to give your home and work PCs the
**same code**; then this one Shortcut works with both.

## 3. Clipboard between iPhone and PC

**Copy to PC** (iPhone clipboard → PC clipboard):
1. New shortcut `Copy to PC`. Add **Get Clipboard**.
2. Add **Get Contents of URL**: `http://droptray.local:8765/text`, Method **POST**,
   Headers `X-Token` = pairing code, `X-Device` = `iPhone`, Request Body **File** → *Clipboard*.

**Paste from PC** (PC clipboard → iPhone clipboard). First turn on **Phone can see what I copy**
in DropTray's Phone tab; until then the PC keeps its clipboard to itself.
1. New shortcut `Paste from PC`.
2. Add **Get Contents of URL**: `http://droptray.local:8765/clipboard?plain=1`,
   Header `X-Token` = pairing code.
3. Add **Copy to Clipboard**.

Make them instant: **Settings → Accessibility → Touch → Back Tap → Double Tap → Copy to PC**
(and Triple Tap → Paste from PC). Tapping the back of the phone then syncs the clipboard.

Without Shortcuts:
- **PC → iPhone:** with **Phone can see what I copy** on, whatever you copy on the PC (text, files
  from Explorer, a screenshot) appears on the phone page under *From PC*, with Copy or Save.
  Or press **Send** on the copied-item card in DropTray's Phone tab.
- **iPhone → PC:** on the phone page, tap **Send what you copied**, then Paste. Photos, files
  and text all work.

## If the phone can't connect

- Run `setup-firewall.ps1` (it asks for administrator).
- The phone must be on the same network, not mobile data or a guest Wi-Fi.
- If `droptray.local` doesn't open, use the IP address shown under the QR code.
