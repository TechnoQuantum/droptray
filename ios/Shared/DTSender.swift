import Foundation
import Observation

/// One batch of files going to a PC: asked, then uploaded two at a time, with live progress.
@MainActor @Observable
final class SendBatch: Identifiable {
    enum Phase: Equatable {
        case asking, sending, done, failed(String), cancelled
    }

    let id = UUID()
    let files: [OutgoingFile]
    let pcName: String
    var phase: Phase = .asking
    var sent: [UUID: Int64] = [:]
    var finished: Set<UUID> = []
    var failed: [UUID: String] = [:]
    var current = ""
    var speed: Double = 0
    var ticket = ""
    var nextIndex = 0
    let started = Date()
    private var sampleTime = Date()
    private var sampleBytes: Int64 = 0

    init(files: [OutgoingFile], pcName: String) {
        self.files = files
        self.pcName = pcName
    }

    var totalBytes: Int64 { files.reduce(0) { $0 + $1.size } }
    var sentBytes: Int64 { sent.values.reduce(0, +) }
    var fraction: Double { totalBytes > 0 ? min(Double(sentBytes) / Double(totalBytes), 1) : 0 }
    var position: Int { min(finished.count + failed.count + 1, files.count) }
    var isActive: Bool { phase == .asking || phase == .sending }
    var eta: Double { speed > 0 ? Double(totalBytes - sentBytes) / speed : 0 }
    var averageSpeed: Double { Double(sentBytes) / max(Date().timeIntervalSince(started), 0.001) }

    func record(_ file: OutgoingFile, bytes: Int64) {
        sent[file.id] = bytes
        let now = Date()
        let dt = now.timeIntervalSince(sampleTime)
        if dt >= 0.5 {
            let instant = Double(sentBytes - sampleBytes) / dt
            speed = speed == 0 ? instant : speed * 0.5 + instant * 0.5
            sampleTime = now
            sampleBytes = sentBytes
        }
    }
}

/// Sends batches one after another. Shared by the app and the Share extension.
@MainActor @Observable
final class Sender {
    private(set) var batch: SendBatch?
    private var queue: [(files: [OutgoingFile], client: DTClient, pcName: String)] = []
    private var task: Task<Void, Never>?
    private var client: DTClient?

    var waiting: Int { queue.count }
    var isBusy: Bool { batch?.isActive ?? false }

    func send(_ files: [OutgoingFile], with client: DTClient, pcName: String) {
        guard !files.isEmpty else { return }
        if isBusy {
            queue.append((files, client, pcName))
        } else {
            start(files, client, pcName)
        }
    }

    func cancel() {
        guard let b = batch, b.isActive else { return }
        b.phase = .cancelled
        task?.cancel()
        queue.removeAll()
        if !b.ticket.isEmpty, let client {
            let ticket = b.ticket
            Task { await client.cancel(ticket: ticket) }
        }
    }

    /// Clears a finished batch from the screen.
    func dismiss() {
        if let b = batch, !b.isActive { batch = nil }
    }

    private func start(_ files: [OutgoingFile], _ client: DTClient, _ pcName: String) {
        let b = SendBatch(files: files, pcName: pcName)
        batch = b
        self.client = client
        task = Task {
            await run(b, client)
            if !queue.isEmpty {
                let next = queue.removeFirst()
                start(next.files, next.client, next.pcName)
            }
        }
    }

    private func run(_ b: SendBatch, _ client: DTClient) async {
        do {
            let reply = try await client.offer(b.files)
            guard b.phase == .asking else { return }
            guard reply.ok, let ticket = reply.ticket else {
                b.phase = .failed(reply.reason ?? "Declined on the PC")
                return
            }
            b.ticket = ticket
            b.phase = .sending
        } catch {
            if b.phase == .asking { b.phase = .failed(error.localizedDescription) }
            return
        }

        await withTaskGroup(of: Void.self) { group in
            for _ in 0..<2 {
                group.addTask { @MainActor in
                    while !Task.isCancelled, b.phase == .sending, b.nextIndex < b.files.count {
                        let file = b.files[b.nextIndex]
                        b.nextIndex += 1
                        b.current = file.name
                        do {
                            try await DTUpload.file(file, ticket: b.ticket, client: client) { bytes in
                                b.record(file, bytes: bytes)
                            }
                            b.record(file, bytes: file.size)
                            b.finished.insert(file.id)
                        } catch {
                            b.failed[file.id] = error.localizedDescription
                        }
                    }
                }
            }
        }
        if b.phase == .sending {
            b.phase = b.finished.isEmpty ? .failed(b.failed.values.first ?? "Nothing was sent") : .done
        }
        // Copies made for sending are no longer needed.
        for f in b.files where f.url.path.contains("/Outgoing/") {
            try? FileManager.default.removeItem(at: f.url.deletingLastPathComponent())
        }
    }
}

/// Streams one file to /upload, reporting bytes sent.
enum DTUpload {
    static func file(_ f: OutgoingFile, ticket: String, client: DTClient,
                     progress: @escaping @MainActor (Int64) -> Void) async throws {
        var r = client.request("/upload", method: "POST", timeout: 3600)
        r.setValue(ticket, forHTTPHeaderField: "X-Ticket")
        r.setValue(f.name.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "file", forHTTPHeaderField: "X-Filename")
        r.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await DTClient.session.upload(for: r, fromFile: f.url, delegate: UploadProgress(progress))
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else {
            let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            throw DTError.server(text?.nilIfEmpty ?? "The PC answered with HTTP \(code)")
        }
    }
}

final class UploadProgress: NSObject, URLSessionTaskDelegate {
    private let report: @MainActor (Int64) -> Void

    init(_ report: @escaping @MainActor (Int64) -> Void) {
        self.report = report
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        let sent = totalBytesSent
        Task { @MainActor in report(sent) }
    }
}
