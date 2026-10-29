import Foundation

/// DropTray's TCP port. A PC may use another one (it moves off a port some other app holds), so the real port
/// always comes from Bonjour or the QR code; these are only guesses for droptray.local and typed addresses.
enum DTPort {
    static let standard = 47820
    static let legacy = 8765        // DropTray 1.0.3 and earlier
}

/// A PC this phone has paired with. `key` is what DropTray on the PC issued when it accepted the pairing.
struct PairedPC: Codable, Identifiable, Hashable {
    let id: String              // the PC's stable id (from /hello and the Bonjour TXT record)
    var name: String
    var key: String
    var lastHost: String        // "192.168.1.20:47820": where it answered last time
    var lastSeen: Date
    var fp: String? = nil       // its certificate's SHA-256 fingerprint: set means HTTPS, pinned to it
}

// MARK: - API replies (see API.md in the repository)

struct HelloInfo: Decodable {
    let app: String
    let version: String?
    let api: Int?
    let id: String
    let name: String
    let mdns: Bool?
    let tls: Bool?
    let fp: String?
}

struct PairReply: Decodable {
    let ok: Bool
    let key: String?
    let id: String?
    let name: String?
    let reason: String?
}

struct PingReply: Decodable {
    let ok: Bool
    let name: String
    let paused: Bool
    let shared: Int
}

struct OfferReply: Decodable {
    let ok: Bool
    let ticket: String?
    let reason: String?
}

struct TextReply: Decodable {
    let ok: Bool
    let copied: Bool?
    let reason: String?
}

struct SharedReply: Decodable {
    let items: [SharedItem]
}

struct SharedItem: Decodable, Identifiable, Hashable {
    let id: String
    let kind: String            // "file" or "text"
    let name: String
    let size: Int
    let when: Double
    let text: String?
    let thumb: Bool?
}

struct ClipReply: Decodable {
    let enabled: Bool
    let seq: Int
    let kind: String?           // "text", "files", "image" or nil
    let text: String?
    let files: [ClipFile]?
    let image: Bool?
}

struct ClipFile: Decodable, Identifiable, Hashable {
    let i: Int
    let name: String
    let size: Int
    let type: String?
    let thumb: Bool?
    var id: Int { i }
}

/// One file on its way to the PC. It is always a local copy, so uploads stream from disk.
struct OutgoingFile: Identifiable, Hashable {
    let id = UUID()
    let url: URL
    let name: String
    let size: Int64
}

enum DTError: LocalizedError {
    case notPaired
    case offline
    case server(String)

    var errorDescription: String? {
        switch self {
        case .notPaired: return "This phone isn't paired with the PC any more. Pair it again."
        case .offline: return "The PC isn't reachable. Is DropTray running there, and is this phone on the same network?"
        case .server(let message): return message
        }
    }
}

enum Format {
    static func bytes(_ n: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: n, countStyle: .file)
    }

    static func speed(_ bytesPerSecond: Double) -> String {
        bytesPerSecond > 0 ? bytes(Int64(bytesPerSecond)) + "/s" : ""
    }

    static func eta(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds > 0 else { return "" }
        if seconds < 60 { return "\(Int(seconds.rounded(.up))) s left" }
        return "\(Int(seconds) / 60) min \(Int(seconds) % 60) s left"
    }

    static func ago(_ date: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .short
        return f.localizedString(for: date, relativeTo: .now)
    }
}

extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
