# TwoDrive Office development

- Do not launch an office editor onto the user's display or change their normal office profile during development.
- LibreOffice integration tests must use `--headless`, a unique `UserInstallation`, and temporary settings/documents.
- ONLYOFFICE Flatpak resets `DISPLAY` and XDG variables supplied via `flatpak run --env`. Set them **inside** a sandbox shell. Disable X11/Wayland sockets and use an authenticated TCP Xvfb display; verify the real process environment, private profile, and private native instance port before interacting.
- Use only a dedicated test process group for cleanup. Never kill all office processes.
- Never test saves against the user's real TwoDrive mount or work files. Do not claim live cloud synchronization based on local mock-mount tests.
- Every automatic save must revalidate the mount and destination, preserve pending revisions on failure, and avoid dialogs.
- Keep SDK compatibility limits and actual verification results in `docs/verification.md`. ONLYOFFICE's native adapter is distinct from the public web plugin API.
