import SwiftUI

/// Pairing: pick a PC Bonjour found, scan its QR code, or type its address.
struct PairingView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    var firstRun: Bool

    @State private var target: Target?
    @State private var scanning = false
    @State private var address = ""
    @State private var error: String?

    struct Target: Identifiable {
        let id = UUID()
        let host: String
        let name: String
        var code = ""
        var fp: String? = nil       // from the QR code: the certificate pairing must find
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                if firstRun { hero }

                VStack(alignment: .leading, spacing: 12) {
                    Text("On this network").font(.headline)
                    let candidates = model.discovery.found.filter { f in !model.pcs.contains { $0.id == f.id } }
                    if candidates.isEmpty {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Looking for DropTray on your PCs…").foregroundStyle(.secondary)
                        }
                        Text("Make sure DropTray is running on the PC and this phone is on the same Wi-Fi. If iOS asked about the local network, choose Allow.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(candidates) { pc in
                        Button {
                            if let host = pc.host { target = Target(host: host, name: pc.name) }
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: "desktopcomputer")
                                    .foregroundStyle(.white)
                                    .frame(width: 40, height: 40)
                                    .background(LinearGradient(colors: [.cyan, .blue], startPoint: .top, endPoint: .bottom),
                                                in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                                VStack(alignment: .leading) {
                                    Text(pc.name).fontWeight(.medium).foregroundStyle(.primary)
                                    Text(pc.host ?? "Finding its address…").font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                            }
                        }
                        .disabled(pc.host == nil)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassCard()

                VStack(alignment: .leading, spacing: 12) {
                    Text("Or scan the QR code").font(.headline)
                    Text("On the PC, open DropTray and choose Show pairing QR.").font(.subheadline).foregroundStyle(.secondary)
                    Button { scanning = true } label: {
                        Label("Scan QR code", systemImage: "qrcode.viewfinder").frame(maxWidth: .infinity)
                    }
                    .secondaryButton().controlSize(.large)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassCard()

                VStack(alignment: .leading, spacing: 12) {
                    Text("Or type the address").font(.headline)
                    HStack {
                        TextField("192.168.1.20", text: $address)
                            .keyboardType(.numbersAndPunctuation)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .padding(12)
                            .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                        Button("Next") {
                            let host = address.contains(":") ? address : address + ":8765"
                            target = Target(host: host.trimmingCharacters(in: .whitespaces), name: address)
                        }
                        .secondaryButton()
                        .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
                .glassCard()

                if let error {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .background(AppBackground())
        .navigationTitle(firstRun ? "" : "Add a PC")
        .toolbar {
            if !firstRun {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
            }
        }
        .task { model.discovery.start() }
        .sheet(item: $target) { t in
            PairSheet(target: t) { dismiss() }
                .presentationDetents([.medium, .large])
        }
        .fullScreenCover(isPresented: $scanning) {
            QRScannerSheet { text in
                scanning = false
                handleScan(text)
            }
        }
    }

    private var hero: some View {
        VStack(spacing: 12) {
            Image(systemName: "arrow.down.to.line")
                .font(.system(size: 34, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 84, height: 84)
                .background(LinearGradient(colors: [.cyan, .blue], startPoint: .top, endPoint: .bottom),
                            in: RoundedRectangle(cornerRadius: 22, style: .continuous))
                .shadow(color: .blue.opacity(0.4), radius: 16, y: 8)
            Text("DropTray").font(.largeTitle.weight(.bold))
            Text("Send photos, videos and files to your PC, and get its clipboard and files back. Pair once with each PC.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 24)
    }

    /// The PC's QR code holds http://<ip>:8765/?pair=<code>: pair at once with that code.
    private func handleScan(_ text: String) {
        guard let url = URL(string: text), let h = url.host else {
            error = "That isn't a DropTray pairing code."
            return
        }
        let host = "\(h):\(url.port ?? 8765)"
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let code = items.first { $0.name == "pair" || $0.name == "t" }?.value ?? ""
        let fp = items.first { $0.name == "fp" }?.value
        target = Target(host: host, name: h, code: code, fp: fp)
    }
}

/// Pairs with one PC: asks it (the PC's user taps Pair), or uses the code shown in DropTray.
struct PairSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let target: PairingView.Target
    var onPaired: () -> Void

    @State private var code = ""
    @State private var asking = false
    @State private var error: String?

    var body: some View {
        @Bindable var model = model
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("Pair with \(target.name)").font(.title2.weight(.bold))
                Text("This phone's name").font(.subheadline).foregroundStyle(.secondary)
                TextField("iPhone", text: $model.deviceName)
                    .padding(12)
                    .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                Button {
                    pair(code: nil)
                } label: {
                    HStack {
                        if asking { ProgressView() }
                        Text(asking ? "Accept the request on the PC" : "Ask the PC to pair")
                    }
                    .frame(maxWidth: .infinity)
                }
                .primaryButton().controlSize(.large).disabled(asking)
                Text("Or type the pairing code shown in DropTray").font(.subheadline).foregroundStyle(.secondary)
                HStack {
                    TextField("Pairing code", text: $code)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .padding(12)
                        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    Button("Pair") { pair(code: code) }.secondaryButton().disabled(code.isEmpty || asking)
                }
                if let error { Text(error).font(.footnote).foregroundStyle(.red) }
                Spacer()
            }
            .padding(20)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
        .onAppear {
            if !target.code.isEmpty { code = target.code; pair(code: target.code) }
        }
    }

    private func pair(code: String?) {
        asking = true
        error = nil
        Task {
            do {
                try await model.pair(host: target.host, code: code, expected: target.fp)
                dismiss()
                onPaired()
            } catch {
                self.error = error.localizedDescription
            }
            asking = false
        }
    }
}
