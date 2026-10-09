import Foundation
import Observation
import UIKit

/// The app's state: paired PCs, which one is reachable now, and the batch being sent.
@MainActor @Observable
final class AppModel {
    enum Status { case searching, connected, paused, offline }

    var pcs: [PairedPC] = DTStore.loadPCs()
    var active: PairedPC?
    var host: String?
    var status: Status = .searching
    var sharedCount = 0
    var deviceName: String = DTStore.deviceName {
        didSet { DTStore.deviceName = deviceName }
    }

    let sender = Sender()
    let discovery = Discovery()
    private var heartbeat: Task<Void, Never>?
    private var background: UIBackgroundTaskIdentifier = .invalid

    /// A client for the PC that answers right now, or nil.
    var client: DTClient? {
        guard let pc = active, let host else { return nil }
        return DTClient(host: host, key: pc.key, device: deviceName)
    }

    var statusText: String {
        switch status {
        case .searching: return "looking"
        case .connected: return "connected"
        case .paused: return "paused"
        case .offline: return "not reachable"
        }
    }

    // MARK: - Presence

    func start() {
        discovery.start()
        heartbeat?.cancel()
        heartbeat = Task {
            while !Task.isCancelled {
                await refresh()
                try? await Task.sleep(for: .seconds(status == .connected || status == .paused ? 5 : 3))
            }
        }
    }

    func stop() {
        heartbeat?.cancel()
        heartbeat = nil
        discovery.stop()
    }

    func refresh() async {
        if let client, let pc = active {
            do {
                let ping = try await client.ping()
                status = ping.paused ? .paused : .connected
                sharedCount = ping.shared
                touch(pc.id, host: client.host)
                return
            } catch DTError.notPaired {
                forget(pc)
            } catch {
                // fall through: look for it (or another paired PC) again
            }
        }
        if pcs.isEmpty {
            status = .searching
            return
        }
        for pc in pcs.sorted(by: { $0.lastSeen > $1.lastSeen }) {
            if let found = await DTLocator.find(pc, hints: discovery.hosts(for: pc.id)) {
                active = pc
                host = found
                status = .connected
                touch(pc.id, host: found)
                return
            }
        }
        status = active == nil ? .searching : .offline
    }

    /// Switch to another paired PC (from the header menu).
    func use(_ pc: PairedPC) async {
        active = pc
        host = nil
        status = .searching
        if let found = await DTLocator.find(pc, hints: discovery.hosts(for: pc.id)) {
            host = found
            status = .connected
            touch(pc.id, host: found)
        } else {
            status = .offline
        }
    }

    private func touch(_ id: String, host: String) {
        guard let i = pcs.firstIndex(where: { $0.id == id }) else { return }
        let changed = pcs[i].lastHost != host || Date().timeIntervalSince(pcs[i].lastSeen) > 600
        pcs[i].lastHost = host
        pcs[i].lastSeen = .now
        if changed { DTStore.savePCs(pcs) }
    }

    // MARK: - Pairing

    /// Pairs with the PC at `host`: with a code at once, or by asking (the PC's user taps Pair).
    func pair(host: String, code: String?) async throws {
        let c = DTClient(host: host, device: deviceName)
        let hello = try await c.hello(timeout: 5)
        let reply = try await c.pair(code: code)
        guard reply.ok, let key = reply.key else { throw DTError.server(reply.reason ?? "The PC didn't pair") }
        let pc = PairedPC(id: reply.id ?? hello.id, name: reply.name ?? hello.name, key: key, lastHost: host, lastSeen: .now)
        pcs.removeAll { $0.id == pc.id }
        pcs.append(pc)
        DTStore.savePCs(pcs)
        active = pc
        self.host = host
        status = .connected
    }

    func forget(_ pc: PairedPC) {
        pcs.removeAll { $0.id == pc.id }
        DTStore.savePCs(pcs)
        if active?.id == pc.id {
            active = nil
            host = nil
            status = .searching
        }
    }

    // MARK: - Sending

    func send(_ files: [OutgoingFile]) {
        guard let client, let pc = active, !files.isEmpty else { return }
        sender.send(files, with: client, pcName: pc.name)
        keepRunningInBackground()
    }

    func sendText(_ text: String) async -> String {
        guard let client else { return "The PC isn't reachable" }
        do {
            let reply = try await client.sendText(text)
            return reply.ok ? (reply.copied == true ? "On the PC clipboard" : "Sent to the PC") : (reply.reason ?? "Not sent")
        } catch {
            return error.localizedDescription
        }
    }

    /// iOS gives an app about half a minute after it leaves the screen; long enough to finish most batches.
    private func keepRunningInBackground() {
        guard background == .invalid else { return }
        background = UIApplication.shared.beginBackgroundTask(withName: "DropTray upload") { [weak self] in
            self?.endBackground()
        }
        Task {
            while sender.isBusy { try? await Task.sleep(for: .seconds(1)) }
            endBackground()
        }
    }

    private func endBackground() {
        if background != .invalid {
            UIApplication.shared.endBackgroundTask(background)
            background = .invalid
        }
    }
}
