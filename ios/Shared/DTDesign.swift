import SwiftUI

// MARK: - Glass

extension View {
    /// A card of Liquid Glass on iOS 26 and later, frosted material before that.
    @ViewBuilder
    func glassCard(_ radius: CGFloat = 26, padding: CGFloat = 18) -> some View {
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        if #available(iOS 26.0, *) {
            self.padding(padding).glassEffect(.regular, in: shape)
        } else {
            self.padding(padding)
                .background(.ultraThinMaterial, in: shape)
                .overlay(shape.strokeBorder(.white.opacity(0.12)))
        }
    }

    @ViewBuilder
    func glassCapsule() -> some View {
        if #available(iOS 26.0, *) {
            self.glassEffect(.regular, in: Capsule())
        } else {
            self.background(.ultraThinMaterial, in: Capsule())
        }
    }

    @ViewBuilder
    func primaryButton() -> some View {
        if #available(iOS 26.0, *) {
            self.buttonStyle(.glassProminent)
        } else {
            self.buttonStyle(.borderedProminent)
        }
    }

    @ViewBuilder
    func secondaryButton() -> some View {
        if #available(iOS 26.0, *) {
            self.buttonStyle(.glass)
        } else {
            self.buttonStyle(.bordered)
        }
    }
}

/// The soft grey backdrop behind every screen, matching the Windows drawer's neutral glass.
struct AppBackground: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let dark = scheme == .dark
        ZStack {
            (dark ? Color(white: 0.09) : Color(white: 0.94))
            RadialGradient(colors: [Color.white.opacity(dark ? 0.07 : 0.7), .clear],
                           center: .topTrailing, startRadius: 10, endRadius: 520)
            RadialGradient(colors: [Color.blue.opacity(dark ? 0.10 : 0.07), .clear],
                           center: .bottomLeading, startRadius: 10, endRadius: 480)
        }
        .ignoresSafeArea()
    }
}

// MARK: - The liquid lens

/// Liquid rising inside a glass card as a transfer progresses; the surface moves only while bytes move.
struct LiquidFill: View {
    var fraction: Double
    var moving: Bool
    var done = false

    var body: some View {
        GeometryReader { geo in
            TimelineView(.animation(minimumInterval: 1 / 30, paused: !moving)) { timeline in
                let phase = timeline.date.timeIntervalSinceReferenceDate * 1.7
                let level = geo.size.height * max(0.04, min(fraction, 1))
                Wave(phase: phase, amplitude: done ? 0 : 5)
                    .fill(LinearGradient(colors: done
                                         ? [Color.green.opacity(0.42), Color.green.opacity(0.58)]
                                         : [Color.cyan.opacity(0.45), Color.blue.opacity(0.6)],
                                         startPoint: .top, endPoint: .bottom))
                    .frame(height: level + 10)
                    .frame(maxHeight: .infinity, alignment: .bottom)
                    .animation(.spring(duration: 0.7), value: fraction)
            }
        }
        .allowsHitTesting(false)
    }
}

struct Wave: Shape {
    var phase: Double
    var amplitude: CGFloat

    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: 0, y: amplitude))
        for x in stride(from: CGFloat(0), through: rect.width, by: 4) {
            let y = amplitude + CGFloat(sin(Double(x) / 28 + phase)) * amplitude
            p.addLine(to: CGPoint(x: x, y: y))
        }
        p.addLine(to: CGPoint(x: rect.width, y: rect.height))
        p.addLine(to: CGPoint(x: 0, y: rect.height))
        p.closeSubpath()
        return p
    }
}

/// The lens while a batch is being asked for, sent, finished or stopped.
struct BatchLens: View {
    let batch: SendBatch
    var waiting = 0
    var onCancel: () -> Void
    var onDone: () -> Void

    var body: some View {
        ZStack {
            LiquidFill(fraction: liquidLevel, moving: batch.phase == .sending, done: batch.phase == .done)
            VStack(alignment: .leading, spacing: 6) {
                content
            }
            .padding(22)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .frame(minHeight: 260)
        .clipShape(RoundedRectangle(cornerRadius: 30, style: .continuous))
        .glassCard(30, padding: 0)
        .animation(.smooth, value: batch.phase)
    }

    private var liquidLevel: Double {
        switch batch.phase {
        case .asking: return 0.06
        case .done: return 1
        case .sending: return batch.fraction
        default: return 0
        }
    }

    @ViewBuilder
    private var content: some View {
        switch batch.phase {
        case .asking:
            header
            Spacer(minLength: 16)
            Text("Waiting for the PC").font(.title.weight(.bold))
            Text("Accept on \(batch.pcName) to start").foregroundStyle(.secondary)
            Text("\(batch.files.count) \(batch.files.count == 1 ? "item" : "items"), \(Format.bytes(batch.totalBytes))")
                .font(.subheadline).foregroundStyle(.secondary)
        case .sending:
            header
            HStack(alignment: .lastTextBaseline) {
                Text("\(Int(batch.fraction * 100))%")
                    .font(.system(size: 60, weight: .bold, design: .rounded))
                    .contentTransition(.numericText())
                Spacer()
                Text("\(batch.position) of \(batch.files.count)").foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            Text("\(Format.bytes(batch.sentBytes)) of \(Format.bytes(batch.totalBytes))").fontWeight(.semibold)
            Text([Format.speed(batch.speed), Format.eta(batch.eta)].filter { !$0.isEmpty }.joined(separator: ", "))
                .foregroundStyle(.secondary)
            Text(batch.current).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
        case .done:
            Image(systemName: "checkmark.circle.fill").font(.system(size: 48)).foregroundStyle(.white, .green)
            Text("Sent \(batch.finished.count) \(batch.finished.count == 1 ? "item" : "items")")
                .font(.title2.weight(.bold)).padding(.top, 8)
            Text("\(Format.bytes(batch.sentBytes)) at \(Format.speed(batch.averageSpeed)) to \(batch.pcName)"
                 + (batch.failed.isEmpty ? "" : ", \(batch.failed.count) failed"))
                .foregroundStyle(.secondary)
            Spacer(minLength: 16)
            Button(action: onDone) { Text("Done").frame(maxWidth: .infinity) }.primaryButton().controlSize(.large)
        case .failed(let reason):
            Image(systemName: "xmark.circle.fill").font(.system(size: 48)).foregroundStyle(.white, .red)
            Text(reason).font(.title3.weight(.semibold)).padding(.top, 8)
            Text("Nothing more was sent").foregroundStyle(.secondary)
            Spacer(minLength: 16)
            Button(action: onDone) { Text("OK").frame(maxWidth: .infinity) }.secondaryButton().controlSize(.large)
        case .cancelled:
            Image(systemName: "stop.circle.fill").font(.system(size: 48)).foregroundStyle(.white, .orange)
            Text("Cancelled").font(.title2.weight(.bold)).padding(.top, 8)
            Text("\(batch.finished.count) of \(batch.files.count) had arrived").foregroundStyle(.secondary)
            Spacer(minLength: 16)
            Button(action: onDone) { Text("OK").frame(maxWidth: .infinity) }.secondaryButton().controlSize(.large)
        }
    }

    private var header: some View {
        HStack {
            Text("To \(batch.pcName)" + (waiting > 0 ? ", \(waiting) more waiting" : ""))
                .font(.subheadline).foregroundStyle(.secondary)
            Spacer()
            Button(role: .destructive, action: onCancel) { Text("Cancel") }
                .secondaryButton().controlSize(.small)
        }
    }
}

// MARK: - Small pieces

/// A coloured tile with the file's extension, for files without a picture.
struct TypeBadge: View {
    let name: String
    var size: CGFloat = 46

    var body: some View {
        let ext = (name as NSString).pathExtension.uppercased()
        RoundedRectangle(cornerRadius: size * 0.24, style: .continuous)
            .fill(color.opacity(0.2))
            .frame(width: size, height: size)
            .overlay(Text(ext.isEmpty ? "FILE" : String(ext.prefix(4)))
                .font(.system(size: size * 0.22, weight: .heavy, design: .rounded))
                .foregroundStyle(color))
    }

    private var color: Color {
        switch (name as NSString).pathExtension.lowercased() {
        case "jpg", "jpeg", "png", "heic", "heif", "gif", "webp": return .purple
        case "mov", "mp4", "m4v": return .pink
        case "m4a", "mp3", "wav", "aac": return .orange
        case "pdf", "doc", "docx", "txt", "pages", "key", "numbers", "xlsx", "pptx": return .blue
        case "zip", "rar", "7z": return .yellow
        default: return .gray
        }
    }
}

/// A picture from the PC (thumbnail URL), or a type badge if there is none.
struct RemoteThumb: View {
    let url: URL?
    let name: String
    var size: CGFloat = 46

    var body: some View {
        if let url {
            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    image.resizable().scaledToFill()
                } else {
                    TypeBadge(name: name, size: size)
                }
            }
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.24, style: .continuous))
        } else {
            TypeBadge(name: name, size: size)
        }
    }
}

/// A short message that slides up from the bottom and goes away by itself.
struct Toast: View {
    @Binding var text: String?

    var body: some View {
        if let text {
            Text(text)
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 18).padding(.vertical, 12)
                .glassCapsule()
                .padding(.bottom, 12)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .task(id: text) {
                    try? await Task.sleep(for: .seconds(2.2))
                    withAnimation { self.text = nil }
                }
        }
    }
}
