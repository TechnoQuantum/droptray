import Foundation
import UIKit
import UniformTypeIdentifiers

/// Turns what the user picked, pasted or shared into local files ready to upload (or text to send).
enum DTOutgoing {
    enum Loaded {
        case file(OutgoingFile)
        case text(String)
    }

    static var folder: URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("Outgoing", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// A fresh folder per file keeps the original name without clashes.
    static func destination(_ name: String) -> URL {
        let dir = folder.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent(name.nilIfEmpty ?? "file")
    }

    static func size(_ url: URL) -> Int64 {
        Int64((try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0)
    }

    /// Copies a file we may only borrow (a picker's or a share's temporary file) into our own folder.
    static func adopt(_ url: URL, name: String? = nil) throws -> OutgoingFile {
        let dest = destination(name ?? url.lastPathComponent)
        try FileManager.default.copyItem(at: url, to: dest)
        return OutgoingFile(url: dest, name: dest.lastPathComponent, size: size(dest))
    }

    static func write(_ data: Data, name: String) throws -> OutgoingFile {
        let dest = destination(name)
        try data.write(to: dest)
        return OutgoingFile(url: dest, name: dest.lastPathComponent, size: Int64(data.count))
    }

    static func pastedName(_ ext: String) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH.mm.ss"
        return "Pasted \(f.string(from: .now)).\(ext.nilIfEmpty ?? "dat")"
    }

    /// What one shared or pasted item holds: a file (photo, video, document) or text (including links).
    static func load(_ provider: NSItemProvider) async -> Loaded? {
        // A web link is text, not a file.
        if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier),
           !provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier),
           let url = try? await provider.loadItem(forTypeIdentifier: UTType.url.identifier) as? URL,
           !url.isFileURL {
            return .text(url.absoluteString)
        }
        for type in [UTType.movie, .image, .audio, .pdf] where provider.hasItemConformingToTypeIdentifier(type.identifier) {
            if let file = await loadFile(provider, type: type) { return .file(file) }
        }
        if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier),
           let text = try? await provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) as? String {
            return .text(text)
        }
        for type in [UTType.fileURL, .data] where provider.hasItemConformingToTypeIdentifier(type.identifier) {
            if let file = await loadFile(provider, type: type) { return .file(file) }
        }
        return nil
    }

    static func loadFile(_ provider: NSItemProvider, type: UTType) async -> OutgoingFile? {
        await withCheckedContinuation { continuation in
            _ = provider.loadFileRepresentation(forTypeIdentifier: type.identifier) { url, _ in
                guard let url else {
                    continuation.resume(returning: nil)
                    return
                }
                // The system deletes `url` when this returns, so copy it now.
                let ext = url.pathExtension
                var name = provider.suggestedName ?? url.lastPathComponent
                if !ext.isEmpty, !name.lowercased().hasSuffix("." + ext.lowercased()) { name += "." + ext }
                if provider.suggestedName == nil, type == .image || type == .movie, url.lastPathComponent.count > 30 {
                    name = pastedName(ext)     // a random temporary name helps nobody
                }
                continuation.resume(returning: try? adopt(url, name: name))
            }
        }
    }

    /// Everything on the iPhone clipboard: photos, videos, files and text. iOS may ask to allow pasting.
    @MainActor
    static func fromPasteboard() async -> [Loaded] {
        var out: [Loaded] = []
        for provider in UIPasteboard.general.itemProviders {
            if let item = await load(provider) { out.append(item) }
        }
        return out
    }
}
