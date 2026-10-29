import Foundation

/// Talks to DropTray on one PC over the local network. Every call is described in API.md.
/// With a pinned fingerprint it uses HTTPS and accepts only that PC's certificate; without, plain HTTP.
struct DTClient {
    let host: String                    // "192.168.1.20:47820" or "droptray.local:47820"
    var key: String = ""
    var device: String = DTStore.deviceName
    var fp: String? = nil

    static var configuration: URLSessionConfiguration {
        let c = URLSessionConfiguration.default
        c.timeoutIntervalForRequest = 30
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        c.waitsForConnectivity = false
        c.httpMaximumConnectionsPerHost = 4
        return c
    }

    /// Plain HTTP: only for PCs running a DropTray without HTTPS.
    static let plain = URLSession(configuration: configuration)

    var session: URLSession { fp.map { DTSessions.session(pinnedTo: $0) } ?? DTClient.plain }
    var scheme: String { fp == nil ? "http" : "https" }
    var encrypted: Bool { fp != nil }

    /// A URL on this PC, with the key as `t=`: images and downloads load without custom headers.
    func url(_ path: String, _ extra: [URLQueryItem] = []) -> URL {
        var c = URLComponents(string: "\(scheme)://\(host)\(path)")!
        var items = (c.queryItems ?? []) + extra
        if !key.isEmpty { items.append(URLQueryItem(name: "t", value: key)) }
        c.queryItems = items.isEmpty ? nil : items
        return c.url!
    }

    func request(_ path: String, method: String = "GET", json: Any? = nil, timeout: TimeInterval = 15) -> URLRequest {
        var r = URLRequest(url: url(path), timeoutInterval: timeout)
        r.httpMethod = method
        if !key.isEmpty { r.setValue(key, forHTTPHeaderField: "X-Token") }
        r.setValue(device, forHTTPHeaderField: "X-Device")
        if let json {
            r.httpBody = try? JSONSerialization.data(withJSONObject: json)
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return r
    }

    func call<T: Decodable>(_ r: URLRequest, as type: T.Type = T.self) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: r)
        } catch {
            if (error as? URLError)?.code == .cancelled { throw CancellationError() }
            throw DTError.offline
        }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 { throw DTError.notPaired }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw DTError.server(String(data: data, encoding: .utf8)?.nilIfEmpty ?? "The PC answered with HTTP \(code)")
        }
    }

    // MARK: - Calls

    func hello(timeout: TimeInterval = 3) async throws -> HelloInfo {
        try await call(request("/hello", timeout: timeout))
    }

    /// With a code: pairs at once. Without: the PC asks its user, and this waits up to 90 s for the answer.
    func pair(code: String?) async throws -> PairReply {
        var body: [String: Any] = ["device": device]
        if let code, !code.isEmpty { body["code"] = code.uppercased() }
        return try await call(request("/pair", method: "POST", json: body, timeout: 100))
    }

    func ping() async throws -> PingReply {
        try await call(request("/ping", timeout: 4))
    }

    /// Announces a batch; the PC's user accepts or declines. A ticket comes back when accepted.
    func offer(_ files: [OutgoingFile]) async throws -> OfferReply {
        let list = files.map { ["name": $0.name, "size": $0.size] as [String: Any] }
        return try await call(request("/offer", method: "POST", json: ["device": device, "files": list], timeout: 100))
    }

    func cancel(ticket: String) async {
        _ = try? await session.data(for: request("/cancel", method: "POST", json: ["ticket": ticket]))
    }

    func sendText(_ text: String) async throws -> TextReply {
        try await call(request("/text", method: "POST", json: ["text": text, "device": device]))
    }

    func clipboard() async throws -> ClipReply {
        try await call(request("/clipboard"))
    }

    func shared() async throws -> [SharedItem] {
        try await call(request("/shared"), as: SharedReply.self).items
    }

    /// Downloads something the PC offers into a temporary file named `name`.
    func download(_ path: String, name: String) async throws -> URL {
        let (tmp, response) = try await session.download(for: request(path, timeout: 900))
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw DTError.server("The PC doesn't share this any more.")
        }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("FromPC", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let dest = dir.appendingPathComponent(name)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.moveItem(at: tmp, to: dest)
        return dest
    }

    /// Ping (best of 5), download (32 MB) and upload (16 MB) speed; the PC shows the result too.
    func speedTest() async throws -> (upMbps: Double, downMbps: Double, pingMs: Double) {
        var best = Double.infinity
        for _ in 0..<5 {
            let t = Date()
            _ = try await ping()
            best = min(best, Date().timeIntervalSince(t) * 1000)
        }
        let n = 32 * 1024 * 1024
        var t = Date()
        let (down, _) = try await session.data(for: request("/speedtest/down?bytes=\(n)", timeout: 120))
        let downMbps = Double(down.count) * 8 / Date().timeIntervalSince(t) / 1e6
        let blob = Data(count: 16 * 1024 * 1024)
        t = Date()
        _ = try await session.upload(for: request("/speedtest/up", method: "POST", timeout: 120), from: blob)
        let upMbps = Double(blob.count) * 8 / Date().timeIntervalSince(t) / 1e6
        let result: [String: Any] = ["up_mbps": upMbps, "down_mbps": downMbps, "ping_ms": best, "device": device]
        _ = try? await session.data(for: request("/speedtest/result", method: "POST", json: result))
        return (upMbps, downMbps, best)
    }
}

/// Finds where a paired PC answers right now: its last address, droptray.local, or Bonjour hints.
enum DTLocator {
    static func find(_ pc: PairedPC, hints: [String] = []) async -> String? {
        var seen = Set<String>()
        let guesses = [DTPort.standard, DTPort.legacy].map { "droptray.local:\($0)" }
        let hosts = ([pc.lastHost] + hints + guesses).filter { !$0.isEmpty && seen.insert($0).inserted }
        return await withTaskGroup(of: String?.self) { group in
            for host in hosts {
                group.addTask {
                    // The id check matters: droptray.local can be a different PC on another network.
                    (try? await DTClient(host: host, fp: pc.fp).hello(timeout: 2.5))?.id == pc.id ? host : nil
                }
            }
            for await found in group {
                if let found {
                    group.cancelAll()
                    return found
                }
            }
            return nil
        }
    }
}
