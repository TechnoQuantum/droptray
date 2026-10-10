# DropTray for iPhone and iPad

A native SwiftUI app for DropTray, plus a Share extension that puts **DropTray** next to AirDrop in the Share sheet.
It uses the same HTTP API as the web page ([API.md](../API.md)), so the PC side needs no changes.

> **Status: first draft, not yet compiled.** It was written on Windows without Xcode. Expect a few compile
> errors on the first build. Paste them into an issue or a chat and they'll get fixed.

## What it does

- **Send:** Photos & Videos (originals, any number), Files, and **Paste**. Paste reads the whole iPhone clipboard,
  so copied photos, videos and files work, not just text. Text and links go too. A liquid progress lens shows the
  transfer, with Cancel.
- **From PC:** the PC's clipboard (when allowed on the PC) and what the PC shared. Files open in Quick Look (share,
  save to Files); photos and videos can go straight to Photos.
- **Pairing:** PCs on your network appear by themselves (Bonjour). Tap one, then *Ask the PC to pair* or type its
  code, or scan the QR code from DropTray on the PC.
- **Several PCs:** the header switches between them; the app finds whichever paired PC is reachable.
- **Share extension:** in any app's Share sheet, pick DropTray and send to your PC without opening the app.
- **More:** speed test, paired PCs (use or forget), this phone's name.
- **Encrypted:** with DropTray 1.0.2 or later on the PC, everything goes over HTTPS pinned to that PC's own
  certificate: no warnings, nothing to install, and another machine can't pretend to be your PC. Pairing from the
  QR code verifies the certificate. Older pairings upgrade by themselves.
- **Look:** Liquid Glass on iOS 26 and later (`glassEffect`, glass buttons), frosted material on iOS 17 to 25.

## Build it

You need a Mac with **Xcode 26** (for the Liquid Glass APIs), an iPhone on iOS 17 or later, and an Apple ID
(a free one works; apps then expire after 7 days and need a rebuild).

1. Install XcodeGen once: `brew install xcodegen`
2. Generate the project:
   ```sh
   cd ios
   xcodegen generate
   open DropTray.xcodeproj
   ```
3. In `project.yml`, set `DROPTRAY_BUNDLE_ID` to something of your own (`com.yourname.droptray`), since bundle IDs
   must be unique to your team. Run `xcodegen generate` again after changing it.
4. In Xcode, select the **DropTray** target → *Signing & Capabilities* → choose your Team. Do the same for the
   **DropTrayShare** target. (Or put your Team ID in `DEVELOPMENT_TEAM` in `project.yml`.)
5. Connect the iPhone, select it as the run destination, and press Run.
   - First time on a free account: on the iPhone, Settings → General → VPN & Device Management → trust your
     developer certificate. Turn on Developer Mode if iOS asks (Settings → Privacy & Security).
6. On first launch, allow **Local Network** (required to find and reach the PC) and, for QR pairing, the camera.

Tip: Settings → DropTray → *Paste from Other Apps* → **Allow**, so Paste doesn't ask every time.

### Without XcodeGen

Create a new iOS App project in Xcode (SwiftUI, Swift), named DropTray. Delete its generated Swift files, then add
the `DropTray` and `Shared` folders to the app target. Add a Share Extension target named DropTrayShare, replace its
files with `ShareExtension`, and add `Shared` to it too. Then copy the Info.plist keys and entitlements listed in
`project.yml` into each target.

## How it's organised

| Folder | What |
|---|---|
| `Shared/` | In both targets: API client (`DTClient`, `DTLocator`), models, Keychain store, batch sender with progress, turning shared/pasted items into files, design pieces (glass, liquid lens) |
| `DropTray/` | The app: `AppModel` (paired PCs, presence, sending), Bonjour `Discovery`, the screens |
| `ShareExtension/` | The Share-sheet entry |
| `project.yml` | The XcodeGen project: targets, Info.plist keys, entitlements |

Paired PCs and their keys live in the Keychain, in a group both targets share, so pairing in the app also makes the
Share extension work.

## Troubleshooting

- **"The PC isn't reachable":** DropTray must run on the PC, both devices must be on the same network, and the PC's
  firewall must allow DropTray (the installer's firewall option, or `install.ps1 -Firewall`).
- **No PCs listed:** Local Network permission was denied. Settings → DropTray → Local Network.
- **The Share extension says "Pair DropTray first":** the shared Keychain group didn't sign. Make sure both targets
  use the same Team, then rebuild.
- **Uploads stop when the app leaves the screen:** iOS gives about 30 seconds in the background. Keep DropTray open
  for very large batches. Background uploads are a planned improvement.
