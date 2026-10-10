import SwiftUI
import UIKit

/// The Share-sheet entry: "DropTray" next to AirDrop in Photos, Files, Safari and other apps.
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? []).flatMap { $0.attachments ?? [] }
        let root = ShareView(providers: providers) { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: nil)
        }
        let host = UIHostingController(rootView: root)
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)
    }
}

struct ShareView: View {
    let providers: [NSItemProvider]
    let finish: () -> Void

    @State private var sender = Sender()
    @State private var pcs = DTStore.loadPCs().sorted { $0.lastSeen > $1.lastSeen }
    @State private var chosen: PairedPC?
    @State private var host: String?
    @State private var looking = true
    @State private var files: [OutgoingFile] = []
    @State private var texts: [String] = []
    @State private var loading = true
    @State private var note: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    if pcs.isEmpty {
                        ContentUnavailableView("Pair DropTray first", systemImage: "desktopcomputer",
                                               description: Text("Open the DropTray app and pair it with your PC, then share again."))
                    } else if let batch = sender.batch {
                        BatchLens(batch: batch, onCancel: { sender.cancel() }, onDone: finish)
                    } else {
                        pcCard
                        summaryCard
                        Button(action: send) {
                            Text(host == nil ? (looking ? "Looking for \(chosen?.name ?? "your PC")…" : "\(chosen?.name ?? "The PC") isn't reachable")
                                 : "Send to \(chosen?.name ?? "PC")")
                                .frame(maxWidth: .infinity)
                        }
                        .primaryButton().controlSize(.large)
                        .disabled(host == nil || loading || (files.isEmpty && texts.isEmpty))
                    }
                    if let note { Text(note).font(.footnote).foregroundStyle(.secondary) }
                }
                .padding(16)
            }
            .background(AppBackground())
            .navigationTitle("DropTray")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        sender.cancel()
                        finish()
                    }
                }
            }
        }
        .task { await prepare() }
    }

    private var pcCard: some View {
        HStack(spacing: 12) {
            Image(systemName: "desktopcomputer")
                .foregroundStyle(.white)
                .frame(width: 42, height: 42)
                .background(LinearGradient(colors: [.cyan, .blue], startPoint: .top, endPoint: .bottom),
                            in: RoundedRectangle(cornerRadius: 11, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(chosen?.name ?? "PC").fontWeight(.semibold)
                HStack(spacing: 6) {
                    Circle().fill(host != nil ? Color.green : (looking ? Color.gray : Color.red)).frame(width: 7, height: 7)
                    Text(host != nil ? "connected" : (looking ? "looking" : "not reachable"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if pcs.count > 1 {
                Menu {
                    ForEach(pcs) { pc in
                        Button(pc.name) { Task { await pick(pc) } }
                    }
                } label: {
                    Text("Change").font(.subheadline)
                }
            }
        }
        .glassCard()
    }

    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            if loading {
                HStack { ProgressView(); Text("Getting the items ready…").foregroundStyle(.secondary) }
            } else {
                if !files.isEmpty {
                    Text("\(files.count) \(files.count == 1 ? "file" : "files"), \(Format.bytes(files.reduce(0) { $0 + $1.size }))")
                        .font(.headline)
                    ForEach(files.prefix(4)) { f in
                        HStack(spacing: 10) {
                            TypeBadge(name: f.name, size: 32)
                            Text(f.name).lineLimit(1).font(.subheadline)
                        }
                    }
                    if files.count > 4 {
                        Text("and \(files.count - 4) more").font(.footnote).foregroundStyle(.secondary)
                    }
                }
                ForEach(texts, id: \.self) { t in
                    Label(t, systemImage: t.hasPrefix("http") ? "link" : "text.quote").lineLimit(3).font(.subheadline)
                }
                if files.isEmpty && texts.isEmpty {
                    Text("Nothing here DropTray can send.").foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func prepare() async {
        for provider in providers {
            switch await DTOutgoing.load(provider) {
            case .file(let f)?: files.append(f)
            case .text(let t)?: texts.append(t)
            case nil: break
            }
        }
        loading = false
        for pc in pcs {
            if let found = await DTLocator.find(pc) {
                chosen = pc
                host = found
                looking = false
                return
            }
        }
        chosen = pcs.first
        looking = false
    }

    private func pick(_ pc: PairedPC) async {
        chosen = pc
        host = nil
        looking = true
        host = await DTLocator.find(pc)
        looking = false
    }

    private func send() {
        guard let pc = chosen, let host else { return }
        let client = DTClient(host: host, key: pc.key, device: DTStore.deviceName)
        let texts = self.texts
        if !texts.isEmpty {
            Task {
                for t in texts { _ = try? await client.sendText(t) }
                if files.isEmpty {
                    note = "Sent to \(pc.name)"
                    try? await Task.sleep(for: .seconds(0.8))
                    finish()
                }
            }
        }
        if !files.isEmpty {
            sender.send(files, with: client, pcName: pc.name)
        }
    }
}
