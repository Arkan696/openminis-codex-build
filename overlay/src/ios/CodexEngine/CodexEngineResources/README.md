# CodexEngine bundle resources (manual review required)

This directory is intentionally source-only. Before an Xcode build, place the user-authorized iOS Mach-O Codex runtime here:

- `CodexRuntime/codex` — arm64 iOS Mach-O, executable mode, independently signed with the host.
- `CodexRuntime/ca-certificates.crt` — fixed Mozilla bundle SHA-256 `427c064216761a1e72a8b30f0d954d08cb6e4435a82091fb865afd20c8497285`.
- `CodexRuntime/text-only.toml` — reviewed config with shell, MCP, code mode, image and model discovery disabled.

Do not put `codex` in iSH/Alpine resources. Do not copy API keys, `auth.json`, or user sessions into this directory. Before publishing, review Codex and third-party redistribution licenses.
