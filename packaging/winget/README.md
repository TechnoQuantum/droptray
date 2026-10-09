# winget

`manifests/` holds DropTray's manifests in the layout of [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs).

- **Before it is in the public catalog**, install from these files (winget asks you to allow local manifests once, as administrator):
  ```powershell
  winget settings --enable LocalManifestFiles
  winget install --manifest packaging\winget\manifests\t\TechnoQuantum\DropTray\1.0.0
  ```
- **To publish**, open a pull request to microsoft/winget-pkgs with the `1.0.0` folder (or run
  `wingetcreate submit` on it). After review, `winget install TechnoQuantum.DropTray` works for everyone.
- **For a new version**, copy the folder, update `PackageVersion`, `InstallerUrl` and `InstallerSha256`
  (from the release's `SHA256SUMS.txt`), and submit again.
