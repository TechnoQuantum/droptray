import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct SendView: View {
    @Environment(AppModel.self) private var model
    @State private var photoItems: [PhotosPickerItem] = []
    @State private var showFiles = false
    @State private var preparing = false
    @State private var text = ""
    @State private var toast: String?
    @FocusState private var typing: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    PCHeader()
                    if let batch = model.sender.batch {
                        BatchLens(batch: batch, waiting: model.sender.waiting,
                                  onCancel: { model.sender.cancel() },
                                  onDone: { withAnimation { model.sender.dismiss() } })
                    } else {
                        idleLens
                    }
                    textCard
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(AppBackground())
            .navigationTitle("DropTray")
            .overlay(alignment: .bottom) { Toast(text: $toast) }
            .onChange(of: photoItems) { _, items in
                guard !items.isEmpty else { return }
                Task { await loadPhotos(items) }
            }
            .fileImporter(isPresented: $showFiles, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
                if case .success(let urls) = result { sendFiles(urls) }
            }
        }
    }

    // MARK: - Idle

    private var idleLens: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 14) {
                Image(systemName: "arrow.up")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 54, height: 54)
                    .background(LinearGradient(colors: [.cyan, .blue], startPoint: .top, endPoint: .bottom), in: Circle())
                VStack(alignment: .leading, spacing: 2) {
                    Text("Send to \(model.active?.name ?? "your PC")").font(.title3.weight(.semibold))
                    Text(model.status == .paused ? "The PC paused receiving" : "Photos, videos and files: any number")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            }
            if preparing {
                HStack { ProgressView(); Text("Preparing…").foregroundStyle(.secondary) }
            }
            VStack(spacing: 10) {
                PhotosPicker(selection: $photoItems, matching: .any(of: [.images, .videos]),
                             preferredItemEncoding: .current, photoLibrary: .shared()) {
                    Label("Photos & Videos", systemImage: "photo.on.rectangle.angled").frame(maxWidth: .infinity)
                }
                .primaryButton()
                HStack(spacing: 10) {
                    Button { showFiles = true } label: {
                        Label("Files", systemImage: "folder").frame(maxWidth: .infinity)
                    }
                    Button { paste() } label: {
                        Label("Paste", systemImage: "doc.on.clipboard").frame(maxWidth: .infinity)
                    }
                }
                .secondaryButton()
            }
            .controlSize(.large)
            .disabled(model.client == nil || model.status == .paused || preparing)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard(30, padding: 22)
    }

    // MARK: - Text

    private var textCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Send text or a link").font(.headline)
            TextField("Type or paste", text: $text, axis: .vertical)
                .lineLimit(3...6)
                .focused($typing)
                .padding(12)
                .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            Button {
                let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !t.isEmpty else { return }
                Task {
                    let result = await model.sendText(t)
                    toast = result
                    if result != "The PC isn't reachable" { text = ""; typing = false }
                }
            } label: {
                Text("Send to PC").frame(maxWidth: .infinity)
            }
            .primaryButton()
            .controlSize(.large)
            .disabled(model.client == nil || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .glassCard()
    }

    // MARK: - Getting files

    private func loadPhotos(_ items: [PhotosPickerItem]) async {
        preparing = true
        var files: [OutgoingFile] = []
        for item in items {
            if let picked = try? await item.loadTransferable(type: PickedFile.self) {
                files.append(picked.file)
            }
        }
        photoItems = []
        preparing = false
        if files.isEmpty {
            toast = "Those items couldn't be read"
        } else {
            model.send(files)
        }
    }

    private func sendFiles(_ urls: [URL]) {
        let files = urls.compactMap { url -> OutgoingFile? in
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            return try? DTOutgoing.adopt(url)
        }
        model.send(files)
    }

    /// The whole iPhone clipboard: photos, videos, files and text. iOS asks once to allow pasting.
    private func paste() {
        preparing = true
        Task {
            let items = await DTOutgoing.fromPasteboard()
            preparing = false
            var files: [OutgoingFile] = []
            var texts: [String] = []
            for item in items {
                switch item {
                case .file(let f): files.append(f)
                case .text(let t): texts.append(t)
                }
            }
            if !files.isEmpty { model.send(files) }
            for t in texts { toast = await model.sendText(t) }
            if items.isEmpty { toast = "The clipboard is empty" }
        }
    }
}

/// A photo or video from the Photos picker, copied to a file we own (videos stay full quality).
struct PickedFile: Transferable {
    let file: OutgoingFile

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .movie) { received in
            PickedFile(file: try DTOutgoing.adopt(received.file))
        }
        FileRepresentation(importedContentType: .image) { received in
            PickedFile(file: try DTOutgoing.adopt(received.file))
        }
        FileRepresentation(importedContentType: .item) { received in
            PickedFile(file: try DTOutgoing.adopt(received.file))
        }
    }
}
