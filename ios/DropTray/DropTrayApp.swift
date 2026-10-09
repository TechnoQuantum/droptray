import SwiftUI

@main
struct DropTrayApp: App {
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
        }
        .onChange(of: phase, initial: true) { _, phase in
            switch phase {
            case .active: model.start()
            case .background: model.stop()
            default: break
            }
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if model.pcs.isEmpty {
            NavigationStack {
                PairingView(firstRun: true)
            }
        } else {
            TabView {
                SendView()
                    .tabItem { Label("Send", systemImage: "arrow.up.circle.fill") }
                FromPCView()
                    .tabItem { Label("From PC", systemImage: "desktopcomputer") }
                    .badge(model.sharedCount)
                MoreView()
                    .tabItem { Label("More", systemImage: "ellipsis.circle") }
            }
        }
    }
}

/// The PC pill at the top of each screen: which PC, whether it's reachable, and a menu to switch.
struct PCHeader: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack {
            Menu {
                ForEach(model.pcs) { pc in
                    Button {
                        Task { await model.use(pc) }
                    } label: {
                        Label(pc.name, systemImage: pc.id == model.active?.id ? "checkmark" : "desktopcomputer")
                    }
                }
            } label: {
                HStack(spacing: 8) {
                    Circle().fill(dot).frame(width: 8, height: 8)
                    Text(model.active?.name ?? "Looking for your PC").fontWeight(.semibold)
                    Text(model.statusText).foregroundStyle(.secondary)
                    if model.pcs.count > 1 {
                        Image(systemName: "chevron.up.chevron.down").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .font(.subheadline)
                .foregroundStyle(.primary)
                .padding(.horizontal, 14).padding(.vertical, 9)
                .glassCapsule()
            }
            Spacer()
        }
    }

    private var dot: Color {
        switch model.status {
        case .connected: return .green
        case .paused: return .orange
        case .offline: return .red
        case .searching: return .gray
        }
    }
}
