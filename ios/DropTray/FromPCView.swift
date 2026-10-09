import Photos
import QuickLook
import SwiftUI
import UniformTypeIdentifiers

/// What the PC offers: its clipboard (when allowed) and the files and text it shared.
struct FromPCView: View {
    @Environment(AppModel.self) private var model
    @State private var clip: ClipReply?
    @State private var items: [SharedItem] = []
    @State private var preview: URL?
    @State private var busy: String?
    @State private var toast: String?
    @AppStorage("hiddenClipSeq") private var hiddenClipSeq = -1

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    PCHeader()
                    clipCard
                    sharedCard
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .background(AppBackground())
            .navigationTitle("From PC")
            .refreshable { await load() }
            .task(id: model.host) {
                while !Task.isCancelled {
                    await load()
                    try? await Task.sleep(for: .seconds(4))
                }
            }
            .quickLookPreview($preview)
            .overlay(alignment: .bottom) { Toast(text: $toast) }
        }
    }

    private func load() async {
        guard let client = model.client else { return }
        if let c = try? await client.clipboard() { clip = c }
        if let s = try? await client.shared() { items = s }
    }

    // MARK: - The PC clipboard

    @ViewBuilder
    private var clipCard: some View {
        if let clip, !(clip.enabled && (clip.kind == nil || clip.seq == hiddenClipSeq)) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Copied on the PC").font(.headline)
                    Spacer()
                    if clip.kind != nil {
                        Button { withAnimation { hiddenClipSeq = clip.seq } } label: { Image(systemName: "xmark") }
                            .secondaryButton().controlSize(.small).accessibilityLabel("Hide")
                    }
                }
                if !clip.enabled {
                    Text("Turn on “Phone can see what I copy” in DropTray’s Phone tab to see the PC clipboard here.")
                        .font(.subheadline).foregroundStyle(.secondary)
                } else if clip.kind == "text", let text = clip.text {
                    Text(text).lineLimit(6).textSelection(.enabled)
                    HStack {
                        if let url = URL(string: text), url.scheme?.hasPrefix("http") == true {
                            Link(destination: url) { Label("Open", systemImage: "safari") }.secondaryButton()
                        }
                        Spacer()
                        Button {
                            UIPasteboard.general.string = text
                            toast = "Copied"
                        } label: { Label("Copy", systemImage: "doc.on.doc") }.primaryButton()
                    }
                } else if clip.kind == "image", let client = model.client {
                    let url = client.url("/clipboard/image", [URLQueryItem(name: "v", value: "\(clip.seq)")])
                    AsyncImage(url: url) { image in
                        image.resizable().scaledToFit()
                    } placeholder: {
                        ProgressView().frame(maxWidth: .infinity, minHeight: 120)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    HStack {
                        Spacer()
                        Button { open("/clipboard/image", name: "Copied image.png", key: "clipimage") } label: {
                            Label("Open", systemImage: "arrow.down.circle")
                        }.secondaryButton()
                        Button { saveToPhotos("/clipboard/image", name: "Copied image.png", key: "clipsave") } label: {
                            Label("Save to Photos", systemImage: "square.and.arrow.down")
                        }.primaryButton()
                    }
                } else if clip.kind == "files", let files = clip.files, let client = model.client {
                    ForEach(files) { f in
                        HStack(spacing: 12) {
                            RemoteThumb(url: f.thumb == true ? client.url("/clipboard/thumb/\(f.i)",
                                                                           [URLQueryItem(name: "v", value: "\(clip.seq)")]) : nil,
                                        name: f.name)
                            VStack(alignment: .leading) {
                                Text(f.name).lineLimit(1)
                                Text(Format.bytes(Int64(f.size))).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            rowButton(key: "clip\(f.i)") { open("/clipboard/file/\(f.i)", name: f.name, key: "clip\(f.i)") }
                        }
                    }
                }
            }
            .glassCard()
        }
    }

    // MARK: - Shared from the PC

    private var sharedCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Shared from the PC").font(.headline)
            if items.isEmpty {
                Text("Nothing yet. On the PC, drop files on DropTray, or send what you copied.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(items) { item in
                HStack(spacing: 12) {
                    if item.kind == "text" {
                        Image(systemName: "text.quote").font(.title3).foregroundStyle(.cyan)
                            .frame(width: 46, height: 46)
                            .background(Color.cyan.opacity(0.15), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
                        Text(item.text ?? "").lineLimit(3)
                        Spacer()
                        Button {
                            UIPasteboard.general.string = item.text
                            toast = "Copied"
                        } label: { Image(systemName: "doc.on.doc") }.primaryButton()
                    } else {
                        RemoteThumb(url: item.thumb == true ? model.client?.url("/shared/\(item.id)/thumb") : nil, name: item.name)
                        VStack(alignment: .leading) {
                            Text(item.name).lineLimit(1)
                            Text("\(Format.bytes(Int64(item.size))), \(Format.ago(Date(timeIntervalSince1970: item.when)))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        rowButton(key: item.id) { open("/shared/\(item.id)", name: item.name, key: item.id) }
                    }
                }
                .contextMenu {
                    if item.kind == "file", isMedia(item.name) {
                        Button { saveToPhotos("/shared/\(item.id)", name: item.name, key: item.id) } label: {
                            Label("Save to Photos", systemImage: "square.and.arrow.down")
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func rowButton(key: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            if busy == key { ProgressView() } else { Image(systemName: "arrow.down.circle") }
        }
        .secondaryButton()
        .disabled(busy != nil)
    }

    // MARK: - Actions

    /// Downloads, then shows it in Quick Look, which has Share, Save to Files and Save to Photos.
    private func open(_ path: String, name: String, key: String) {
        guard let client = model.client else { return }
        busy = key
        Task {
            defer { busy = nil }
            do { preview = try await client.download(path, name: name) } catch { toast = error.localizedDescription }
        }
    }

    private func saveToPhotos(_ path: String, name: String, key: String) {
        guard let client = model.client else { return }
        busy = key
        Task {
            defer { busy = nil }
            do {
                let file = try await client.download(path, name: name)
                try await PhotoSaver.save(file)
                toast = "Saved to Photos"
            } catch {
                toast = error.localizedDescription
            }
        }
    }

    private func isMedia(_ name: String) -> Bool {
        guard let type = UTType(filenameExtension: (name as NSString).pathExtension) else { return false }
        return type.conforms(to: .image) || type.conforms(to: .movie)
    }
}

enum PhotoSaver {
    static func save(_ url: URL) async throws {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else {
            throw DTError.server("Allow DropTray to add to Photos in Settings")
        }
        let isVideo = UTType(filenameExtension: url.pathExtension)?.conforms(to: .movie) ?? false
        try await PHPhotoLibrary.shared().performChanges {
            let request = PHAssetCreationRequest.forAsset()
            request.addResource(with: isVideo ? .video : .photo, fileURL: url, options: nil)
        }
    }
}
