import Foundation
import Security

/// Paired PCs and this phone's name. They live in the Keychain, in a group the app and the
/// Share extension both belong to, so pairing once works in both.
enum DTStore {
    private static let service = "DropTray"

    /// "<TeamID>.<bundle id>.shared", filled in by the build (see project.yml). Nil when unsigned.
    private static var group: String? {
        guard let g = Bundle.main.object(forInfoDictionaryKey: "DropTrayKeychainGroup") as? String,
              !g.isEmpty, !g.contains("$(") else { return nil }
        return g
    }

    static func loadPCs() -> [PairedPC] {
        guard let data = read("pcs") else { return [] }
        return (try? JSONDecoder().decode([PairedPC].self, from: data)) ?? []
    }

    static func savePCs(_ pcs: [PairedPC]) {
        if let data = try? JSONEncoder().encode(pcs) {
            write("pcs", data)
        }
    }

    static var deviceName: String {
        get { read("device").flatMap { String(data: $0, encoding: .utf8) }?.nilIfEmpty ?? defaultName }
        set { write("device", Data(newValue.utf8)) }
    }

    /// Not UIDevice: that is main-thread only, and clients read this from background tasks.
    /// The name is editable in More.
    static let defaultName = "iPhone"

    // MARK: - Keychain

    private static func query(_ account: String, group: String?) -> [String: Any] {
        var q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if let group { q[kSecAttrAccessGroup as String] = group }
        return q
    }

    private static func read(_ account: String) -> Data? {
        for g in [group, nil] {
            var q = query(account, group: g)
            q[kSecReturnData as String] = true
            q[kSecMatchLimit as String] = kSecMatchLimitOne
            var out: AnyObject?
            if SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data {
                return data
            }
        }
        return nil
    }

    private static func write(_ account: String, _ data: Data) {
        // Try the shared group first; without the entitlement (an unusual signing setup), fall back to
        // the app's own Keychain so pairing still works in the app.
        for g in [group, nil] {
            let q = query(account, group: g)
            let attrs: [String: Any] = [
                kSecValueData as String: data,
                kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
            ]
            var status = SecItemUpdate(q as CFDictionary, attrs as CFDictionary)
            if status == errSecItemNotFound {
                var add = q
                for (k, v) in attrs { add[k] = v }
                status = SecItemAdd(add as CFDictionary, nil)
            }
            if status == errSecSuccess { return }
        }
    }
}
