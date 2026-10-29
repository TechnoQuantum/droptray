# DropTray API (version 1)

Everything the phone page does goes through this API, so a native iOS app can do the same.
Plain HTTP/1.1 with JSON, on one TCP port of the PC.

## The port

DropTray listens on **47820** by default (8765 up to version 1.0.3). If another app holds the port when
DropTray starts, it takes the next free one from 47821 to 47840, or else any port Windows gives it, and keeps
that port from then on. The user can also set one in Settings → Port, or press Auto. So never assume a port:
take it from Bonjour (the SRV record) or from the QR code, remember where the PC answered last, and look again
with Bonjour when that stops working. `droptray.local:47820` is only a guess.

## Finding the PC

- **Bonjour:** browse `_droptray._tcp`. TXT record: `id` (stable PC id), `name`, `api` (this version), `path`.
  The PC also answers for the host name `droptray.local`.
- **Fallback:** the IP and port in the QR code (`http://<ip>:<port>/?pair=<code>`).

`GET /hello` (no auth) identifies the PC; use `id` to pick the stored key when several PCs are paired:

```json
{"app": "DropTray", "version": "1.0.4", "api": 1, "features": ["pair", "offer", ..., "tls"], "id": "e676720b0973",
 "name": "Prometheus", "mdns": true, "tls": true, "fp": "7a57b795…"}
```

## Encryption (HTTPS on the same port)

The port speaks both plain HTTP and HTTPS: a connection that opens with a TLS handshake is encrypted, anything
else is plain HTTP. Browsers use HTTP; apps should use HTTPS.

The PC has its own self-signed certificate (EC P-256, valid 10 years, kept in `%APPDATA%\DropTray\tls`). Its
SHA-256 fingerprint (of the DER certificate, lowercase hex) is published three ways:
- in the pairing QR code: `http://<ip>:<port>/?pair=<code>&fp=<fingerprint>`, the trustworthy way;
- in `/hello` as `"tls": true, "fp": "<fingerprint>"`;
- as the certificate itself, during the TLS handshake.

Clients pin it: accept exactly that certificate and nothing else, so no certificate authority is involved and
no warning is ever shown. Pairing from the QR code verifies the fingerprint. Pairing without it records the
certificate seen at that moment (trust on first use) and pins it from then on.

## Pairing and auth

`POST /pair` (no auth), JSON `{"device": "My iPhone", "code": "0B915C"}`:
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
