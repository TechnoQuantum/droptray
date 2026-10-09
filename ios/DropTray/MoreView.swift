import SwiftUI

struct MoreView: View {
    @Environment(AppModel.self) private var model
    @State private var speed: (up: Double, down: Double, ping: Double)?
    @State private var testing = false
    @State private var testError: String?
    @State private var addingPC = false

    var body: some View {
        @Bindable var model = model
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    PCHeader()

                    VStack(alignment: .leading, spacing: 14) {
                        Text("Connection speed").font(.headline)
                        HStack(spacing: 10) {
                            stat(speed.map { String(format: "%.0f", $0.up) } ?? "–", "Upload Mbps", .purple)
                            stat(speed.map { String(format: "%.0f", $0.down) } ?? "–", "Download Mbps", .cyan)
                            stat(speed.map { String(format: "%.0f", $0.ping) } ?? "–", "Ping ms", .green)
                        }
                        if let testError { Text(testError).font(.footnote).foregroundStyle(.red) }
                        Button {
                            runSpeedTest()
                        } label: {
                            HStack {
                                if testing { ProgressView() }
                                Text(testing ? "Testing" : "Run speed test")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .secondaryButton().controlSize(.large)
                        .disabled(testing || model.client == nil)
                    }
                    .glassCard()

                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Paired PCs").font(.headline)
                            Spacer()
                            Button { addingPC = true } label: { Label("Add", systemImage: "plus") }
                                .secondaryButton().controlSize(.small)
                        }
                        Text("DropTray connects to whichever of them is on the current network.")
                            .font(.subheadline).foregroundStyle(.secondary)
                        ForEach(model.pcs) { pc in
                            HStack(spacing: 12) {
                                Image(systemName: "desktopcomputer")
                                    .foregroundStyle(.white)
                                    .frame(width: 40, height: 40)
                                    .background(LinearGradient(colors: [.cyan, .blue], startPoint: .top, endPoint: .bottom),
                                                in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                                VStack(alignment: .leading) {
                                    Text(pc.name).fontWeight(.medium)
                                    Text(pc.id == model.active?.id && model.status == .connected
                                         ? "Connected now" : "Seen \(Format.ago(pc.lastSeen))")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Menu {
                                    Button { Task { await model.use(pc) } } label: { Label("Use this PC", systemImage: "checkmark.circle") }
                                    Button(role: .destructive) { model.forget(pc) } label: { Label("Forget", systemImage: "trash") }
                                } label: {
                                    Image(systemName: "ellipsis").frame(width: 32, height: 32)
                                }
                            }
                        }
                    }
                    .glassCard()

                    VStack(alignment: .leading, spacing: 10) {
                        Text("This phone").font(.headline)
                        TextField("Name the PC shows", text: $model.deviceName)
                            .padding(12)
                            .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                        Text("Shown on the PC with everything you send.").font(.footnote).foregroundStyle(.secondary)
                    }
                    .glassCard()

                    VStack(alignment: .leading, spacing: 8) {
                        Text("About").font(.headline)
                        Text("DropTray \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")")
                        Link("Source and help on GitHub", destination: URL(string: "https://github.com/TechnoQuantum/droptray")!)
                        Text("Tip: in Settings → DropTray → Paste from Other Apps, choose Allow, and Paste won't ask each time.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .glassCard()
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .background(AppBackground())
            .navigationTitle("More")
            .sheet(isPresented: $addingPC) {
                NavigationStack { PairingView(firstRun: false) }
            }
        }
    }

    private func stat(_ value: String, _ label: String, _ color: Color) -> some View {
        VStack(spacing: 2) {
            Text(value).font(.title.weight(.bold)).foregroundStyle(color).contentTransition(.numericText())
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func runSpeedTest() {
        guard let client = model.client else { return }
        testing = true
        testError = nil
        Task {
            do {
                let r = try await client.speedTest()
                withAnimation { speed = (r.upMbps, r.downMbps, r.pingMs) }
            } catch {
                testError = error.localizedDescription
            }
            testing = false
        }
    }
}
