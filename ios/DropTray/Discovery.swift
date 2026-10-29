import Foundation
import Network
import Observation

/// Finds DropTray PCs on the network with Bonjour (_droptray._tcp), and resolves their addresses.
@MainActor @Observable
final class Discovery {
    struct Found: Identifiable, Hashable {
        let id: String          // the PC's id from the TXT record
        let name: String
        var host: String?       // "192.168.1.20:47820" once resolved
    }

    private(set) var found: [Found] = []
    private var browser: NWBrowser?
    private var resolving: Set<String> = []

    func start() {
        guard browser == nil else { return }
        let b = NWBrowser(for: .bonjourWithTXTRecord(type: "_droptray._tcp", domain: nil), using: .tcp)
        b.browseResultsChangedHandler = { [weak self] results, _ in
            Task { @MainActor in self?.update(results) }
        }
        b.start(queue: .main)
        browser = b
    }

    func stop() {
        browser?.cancel()
        browser = nil
    }

    func hosts(for id: String) -> [String] {
        found.filter { $0.id == id }.compactMap(\.host)
    }

    private func update(_ results: Set<NWBrowser.Result>) {
        var next: [Found] = []
        for result in results {
            var id = "", name = ""
            if case .bonjour(let txt) = result.metadata {
                id = txt["id"] ?? ""
                name = txt["name"] ?? ""
            }
            if case .service(let service, _, _, _) = result.endpoint {
                if name.isEmpty { name = service }
                if id.isEmpty { id = service }
            }
            let known = found.first { $0.id == id }?.host
            next.append(Found(id: id, name: name, host: known))
            if known == nil { resolve(result.endpoint, id: id) }
        }
        found = next.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    /// Connects briefly to learn the service's IPv4 address and port.
    private func resolve(_ endpoint: NWEndpoint, id: String) {
        guard !resolving.contains(id) else { return }
        resolving.insert(id)
        let params = NWParameters.tcp
        if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options {
            ip.version = .v4
        }
        let connection = NWConnection(to: endpoint, using: params)
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                if case .hostPort(let host, let port) = connection.currentPath?.remoteEndpoint {
                    var h = "\(host)"
                    if let i = h.firstIndex(of: "%") { h = String(h[..<i]) }
                    let address = h.contains(":") ? "[\(h)]:\(port)" : "\(h):\(port)"
                    Task { @MainActor in self?.set(id: id, host: address) }
                }
                connection.cancel()
            case .failed, .waiting:
                connection.cancel()
                Task { @MainActor in self?.resolving.remove(id) }
            default:
                break
            }
        }
        connection.start(queue: .global(qos: .utility))
    }

    private func set(id: String, host: String) {
        resolving.remove(id)
        if let i = found.firstIndex(where: { $0.id == id }) { found[i].host = host }
    }
}
