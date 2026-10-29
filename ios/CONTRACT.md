# DropTray iOS app: contract with the PC

This document sets out what the iOS app (`ios/`) and DropTray on Windows (`core.py`) promise each other. [API.md](../API.md)
lists the endpoints. This file says how the app must use them: finding the PC, trusting it, pairing, sending,
handling errors and staying compatible. When the PC changes, check this file. When the app changes, follow it.

**Status: draft 1.** It covers API version 1, the PC from 1.0.4 and the app at 1.1.0 (not yet compiled).

## 1. Versions

| | Minimum | Notes |
|---|---|---|
| iOS | 17.0 | Liquid Glass on iOS 26 and later, behind `#available` |
| DropTray on the PC | 1.0.0 (plain HTTP) | 1.0.2 adds HTTPS, 1.0.4 adds port changes |
| API | `api: 1` | from `/hello` and the Bonjour TXT record |

The app must refuse a PC whose `api` is higher than it knows, with the message "Update the DropTray app".
The PC keeps `api: 1` working until a breaking change, and then raises the number.

## 2. Who promises what

| The PC promises | The app promises |
|---|---|
| Announces `_droptray._tcp` over Bonjour, with TXT `id`, `name`, `api`, `path`, and its real port in SRV | Gets the port from Bonjour or the QR code, never from a hard-coded value (§3) |
| Keeps its `id` for good, through renames, port changes and new IP addresses | Treats `id` as the PC's identity. Name, IP and port are only hints |
| Uses the same certificate for 10 years, and lists its fingerprint in the QR code and in `/hello` | Pins that fingerprint and accepts no other certificate (§4) |
| Asks its user before any batch arrives, unless they turned that off | Shows "Waiting for the PC" while `/offer` waits, for up to 100 s |
| Answers `401` only when the key really is unknown or revoked | Forgets that PC after a `401` and asks to pair again (§8) |
| Ignores unknown JSON fields and headers | Ignores unknown fields and `features` entries |

## 3. Finding the PC

The PC's port is **not fixed**. It is 47820 by default (8765 up to 1.0.3), but DropTray moves to the next free port
if another app takes it, and the user can change it. The app must handle a PC that comes back on a different port.

To find a paired PC (`DTLocator.find`), try all of these at once and keep the first that answers `/hello` with the
**same `id`**:
1. `lastHost`: where it answered last time (stored per PC);
2. Bonjour results whose TXT `id` matches (`Discovery.hosts(for:)`);
3. guesses: `droptray.local:47820`, then `droptray.local:8765`.

Each try has a 2.5 s timeout. The `id` check matters because `droptray.local` can be a different PC on another
network, such as the work PC when you're at home. After every successful call, update `lastHost`
(`AppModel.touch`).

A typed address without a port gets `:47820`. A scanned QR code always carries its own port.

## 4. Trust: pinned HTTPS

- One port serves both: a connection that opens with a TLS handshake gets HTTPS, anything else gets plain HTTP.
- If a stored PC has `fp`, every request uses `https://` with a `URLSession` that accepts **only** a server
  certificate whose SHA-256 of the DER bytes (lowercase hex) equals `fp` (`DTSessions.session(pinnedTo:)`). The app
  never falls back to HTTP for that PC.
- **During pairing:**
  - From a QR code with `fp=`, the certificate presented must match it. Otherwise pairing fails with "This isn't
    the PC from the QR code".
  - Without a QR code, the app pins the certificate it sees at first contact (trust on first use).
- A PC paired over HTTP that later offers HTTPS (an older DropTray, since updated) moves up once: confirm the `id`,
  then store `fp` (`AppModel.upgradeToHTTPS`). Nothing ever moves a PC back down to HTTP.
- ATS: `NSAllowsLocalNetworking` permits plain HTTP to local hosts. The pinned session doesn't depend on it.

## 5. Pairing

1. `POST /pair {"device": <phone name>, "code": <code or omitted>}`, timeout 100 s. Without a code, the PC user
   presses Pair, which can take up to 90 s.
2. `{"ok": true, "key", "id", "name"}` → store a `PairedPC {id, name, key, lastHost, lastSeen, fp}` in the
   Keychain. If a PC with that `id` is already stored, replace it; don't add a second one.
3. `{"ok": false, "reason"}` → show `reason` as it is.

Never store the pairing code. The device `key` is the only credential. Requests send it as `X-Token`, never in the
URL, except for the URLs the app opens in Quick Look.

## 6. Every request

| | |
|---|---|
| Headers | `X-Token: <key>`, `X-Device: <phone name>` (≤ 40 characters; the PC labels history and notifications with it) |
| JSON | `Content-Type: application/json`, UTF-8 |
| Timeouts | `/hello` 2.5–5 s, `/ping` 4 s, `/pair` and `/offer` 100 s, `/upload` 3600 s, downloads 900 s, others 15 s |
| Presence | `GET /ping` every 5 s in the foreground (every 3 s while searching). The PC shows the phone as online for 20 s after a ping |

## 7. Flows

**Send files**
1. `POST /offer {"device", "files": [{"name", "size"}]}`. Returns `ticket`, or `ok: false` with `reason`
   (declined, or paused).
2. Then `POST /upload` for each file, with headers `X-Ticket` and `X-Filename` (percent-encoded), and the raw
   bytes as the body.
   - At most **2 at a time**.
   - The ticket lasts 30 minutes.
   - A `200` gives `saved_as`, which can differ from the name sent.
3. Cancel: `POST /cancel {"ticket"}`, then stop the uploads still running.
4. One failed file doesn't stop the rest. Report the result as done, partly failed, or failed.

**Send text and links:** `POST /text {"text", "device"}` → `copied` says whether it landed on the PC clipboard.

**Paste:** read the whole pasteboard. Files, photos and videos go through the file flow above; plain text goes
through `/text`.

**From PC**
- `GET /clipboard` answers `{"enabled": false}` while the user hasn't allowed clipboard sharing. Show that as a
  quiet hint, not an error.
- Poll it and compare `seq`.
- `GET /shared` lists what the PC shared. Downloads use `/shared/<id>` and `/clipboard/file/<i>`, with
  `?inline=1` for viewing.

**Speed test:** download 32 MB, upload 16 MB, then `POST /speedtest/result` so the PC shows it too.

## 8. Errors

| From the PC | The app does |
|---|---|
| `401` | `DTError.notPaired`: forget that PC (`AppModel.forget`) and offer to pair again |
| `403` on `/upload` | Show the text body (for example "Declined on the PC") |
| `403` on `/clipboard/file`, `/thumb` or `/image` | Sharing was turned off since the last poll: refresh `/clipboard` |
| `404` on `/shared/<id>` | It's no longer shared: refresh the list |
| `409` on `/upload` | The batch was cancelled on the PC: stop the batch, without marking it as an error |
| `500`, or anything else | Show the body text, or "The PC answered with HTTP n" |
| Timeout or connection refused | Find the PC again (§3). Then show `DTError.offline` |
| A certificate that doesn't match the pin | Treat it like offline, and never retry over HTTP |

## 9. What the app stores

The Keychain stores paired PCs and the phone's name in the access group `<TeamID>.<bundle id>.shared`, which the
app and the Share extension share. Nothing goes in `UserDefaults` or files, apart from temporary copies made for
sending (in `Outgoing/`), which are deleted after each batch.

## 10. Share extension

- Same Keychain group, same `DTClient` and the same sending flow, so the same rules apply.
- It has no Bonjour browser and checks only `lastHost` and the `droptray.local` guesses. If the PC changed port
  since the app last ran, the extension can't find it until the app runs once. See §12.
- iOS gives an extension limited memory and time, so it stays small: the activation rule caps it at 200 images,
  50 movies and 100 files.

## 11. Permissions (Info.plist)

| Key | Why |
|---|---|
| `NSLocalNetworkUsageDescription`, `NSBonjourServices: [_droptray._tcp]` | Find and reach the PC |
| `NSCameraUsageDescription` | Scan the pairing QR code |
| `NSPhotoLibraryAddUsageDescription` | Save photos and videos from the PC |
| `NSAppTransportSecurity.NSAllowsLocalNetworking` | Plain HTTP to PCs paired before HTTPS |

## 12. Known gaps (to do)

- **Share extension and port changes:** add a short, one-time Bonjour lookup (NWBrowser, about 2 s) to the
  extension, or have the app refresh `lastHost` from Bonjour whenever it comes to the foreground.
- **`api` check:** the app decodes `api` but doesn't yet refuse a newer one (§1).
- **Background uploads:** uploads run in the foreground, plus about 30 s of background time. A background
  `URLSession` would need `/upload` to accept uploads from a file, which it already does, and a ticket that lasts
  long enough, which it does too, at 30 minutes.

## 13. Changing the contract

- **PC side:** a new endpoint or field means a new `features` entry or new optional fields, with `api` unchanged.
  Removing or changing the meaning of something means raising `api`, and keeping the old behaviour for at least
  one release.
- **App side:** never depend on a field the PC doesn't list here or in API.md. When you add one, add it to both
  files in the same change.
