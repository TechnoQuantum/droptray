# DropTray API (version 1)

Everything the phone page does goes through this API, so a native iOS app can do the same.
Plain HTTP/1.1 with JSON, on TCP 8765 of the PC.

## Finding the PC

- **Bonjour:** browse `_droptray._tcp`. TXT record: `id` (stable PC id), `name`, `api` (this version), `path`.
  The PC also answers for the host name `droptray.local`.
- **Fallback:** the IP and port in the QR code (`http://<ip>:8765/?pair=<code>`).

`GET /hello` (no auth) identifies the PC; use `id` to pick the stored key when several PCs are paired:

```json
{"app": "DropTray", "api": 1, "features": ["pair", "offer", ...], "id": "e676720b0973", "name": "Prometheus", "mdns": true}
```

## Pairing and auth

`POST /pair` (no auth), JSON `{"device": "Emre's iPhone", "code": "0B915C"}`:
- with `code`: pairs immediately if the code matches.
- without `code`: the PC asks its user (Pair / Decline); the request waits up to 90 s.

Answer: `{"ok": true, "key": "<device key>", "id": "<pc id>", "name": "<pc name>", "device_id": "..."}`
or `{"ok": false, "reason": "..."}`.

Every other call sends the key as header `X-Token: <key>` (or `?t=<key>`). The pairing code itself is
also accepted as a token, which is how iOS Shortcuts authenticate. A revoked key gets `401`.
Optional header `X-Device: <name>` labels the sender in notifications and history.

## Sending files to the PC

1. `POST /offer` `{"device": "...", "files": [{"name": "IMG_1.HEIC", "size": 3400000}, ...]}`
   → the PC user accepts (or it's automatic) →
   `{"ok": true, "ticket": "...", "batch": "..."}` or `{"ok": false, "reason": "Declined on the PC"}`.
2. For each file, `POST /upload` with headers `X-Ticket: <ticket>`, `X-Filename: <url-encoded name>`,
   body = the raw file bytes (`Content-Length` set). Up to 2 in parallel works well.
   → `{"ok": true, "saved_as": "IMG_1.HEIC", "bytes": 3400000, "seconds": 0.41}`.
   `409` means the batch was cancelled on the PC.
3. To stop: `POST /cancel` `{"ticket": "..."}`.

Without a ticket, `/upload` asks the user per file (Shortcuts do this). `multipart/form-data`
bodies (first file part) and `Transfer-Encoding: chunked` are accepted too.

## Text and the clipboard

- `POST /text` `{"text": "...", "device": "..."}` (or a `text/plain` body): lands on the PC,
  copied to its clipboard if the user allows. → `{"ok": true, "copied": true}`
- `GET /clipboard` → what is copied on the PC, only if the user turned on clipboard sharing:
  `{"enabled": true, "seq": 42, "kind": "text" | "files" | "image" | null, "text": "...",
    "files": [{"i": 0, "name": "...", "size": 123, "type": "image", "thumb": true}], "image": true}`.
  `seq` changes whenever the clipboard does; poll and compare.
- `GET /clipboard/file/<i>` (add `?inline=1` to view), `GET /clipboard/thumb/<i>`, `GET /clipboard/image`.
- `GET /clipboard?plain=1` → the copied text as `text/plain` (for the "Paste from PC" Shortcut).

## Files shared from the PC

- `GET /shared` → `{"items": [{"id", "kind": "file" | "text", "name", "size", "when", "text", "thumb"}]}`
- `GET /shared/<id>` (attachment; `?inline=1` to view), `GET /shared/<id>/thumb`.

## Presence and speed

- `GET /ping` → `{"ok", "name", "id", "paused", "shared": <count>, "device": "<your name>"}`.
  The PC shows a phone as online if it pinged in the last 20 s; ping every 5 s while in the foreground.
- `GET /speedtest/down?bytes=N` (≤ 256 MB), `POST /speedtest/up` (any body),
  `POST /speedtest/result` `{"up_mbps", "down_mbps", "ping_ms", "device"}` to show results on the PC.

## Compatibility

`api` in `/hello` and the Bonjour TXT record rises only on breaking changes. New fields and new
`features` entries can appear at any time; ignore what you don't know.
