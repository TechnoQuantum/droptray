import CryptoKit
import Foundation
import Security

/// HTTPS to one PC, accepting exactly its own certificate: the one whose SHA-256 fingerprint was learned at
/// pairing (from the QR code, or seen on first contact). No certificate authority, so nothing to install and
/// no warnings, and a different certificate (another machine pretending to be the PC) is refused.
final class PinnedTrust: NSObject, URLSessionDelegate {
    let expected: String?            // nil: first contact, accept and record what the PC presents
    private(set) var seen: String?

    init(expected: String?) {
        self.expected = expected?.lowercased()
    }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        let fingerprint = PinnedTrust.fingerprint(leaf)
        seen = fingerprint
        if expected == nil || expected == fingerprint {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }

    static func fingerprint(_ certificate: SecCertificate) -> String {
        let der = SecCertificateCopyData(certificate) as Data
        return SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
    }
}

enum DTSessions {
    private static var pinned: [String: URLSession] = [:]
    private static let lock = NSLock()

    /// One session per pinned PC, reused for every request to it.
    static func session(pinnedTo fingerprint: String) -> URLSession {
        lock.lock()
        defer { lock.unlock() }
        if let s = pinned[fingerprint] { return s }
        let s = URLSession(configuration: DTClient.configuration, delegate: PinnedTrust(expected: fingerprint),
                           delegateQueue: nil)
        pinned[fingerprint] = s
        return s
    }

    /// First contact over HTTPS: whatever certificate the PC presents is accepted and its fingerprint returned,
    /// with the PC's /hello. Nil when the PC has no HTTPS (an older DropTray) or can't be reached.
    static func firstContact(host: String) async -> (hello: HelloInfo, fingerprint: String)? {
        let trust = PinnedTrust(expected: nil)
        let session = URLSession(configuration: DTClient.configuration, delegate: trust, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        guard let url = URL(string: "https://\(host)/hello"),
              let result = try? await session.data(for: URLRequest(url: url, timeoutInterval: 5)),
              let hello = try? JSONDecoder().decode(HelloInfo.self, from: result.0),
              let fingerprint = trust.seen else { return nil }
        return (hello, fingerprint)
    }
}
