## SPEC #2: catalog diagnostics and endpoint compatibility

- Port the catalog diagnostic-message fix from xspeed1989/opencode-go-cliproxyapi commit 8e38b5d5d28e8a7d7eb535c8c1c42d2405c8e81b. Excluded model IDs/reasons are now visible with CPA's logging field allowlist; IDs stay quoted to prevent log injection.
- Port the inference endpoint default from tympom/opencode-go-cliproxyapi commit feb456e5b3b85b2a23c7f5aba6bcd6c67dfc05d6 without adopting unrelated SDK/registry changes. Explicit custom base URLs remain supported.
- Existing installations must change an explicitly configured legacy base URL themselves; a changed default does not override configuration.
- Known host limitation (CPA v8.0.15): successful background plugin catalog refresh does not republish per-auth model registrations after an initially failed discovery. A controlled restart or configuration resync can restore registration. This patch does not claim to fix the host synchronization mechanism.
- Windows deployment tooling verifies backups and post-start model availability, and restores the prior plugin/configuration on failure. See SPEC issue #2 for execution evidence.

## What's Changed

- Support CLIProxyAPI v8 and its generic plugin quota API.
- Return rolling, weekly, and monthly quotas for the credential selected by CLIProxyAPI.
- Advertise the quota provider as **OpenCode Go**.
- Keep the fork's compatibility quota page and credential management route alongside native quota endpoints.
- Accept an optional `name` for each API key.
- Use readable account labels and filenames instead of key digests.
- Preserve existing auth IDs, custom labels, filenames, and host metadata.
- Prevent duplicate auth records at cold start when readable filenames exist.
- Reject invalid quota values and redact upstream request errors.

## Upgrade

1. Upgrade CLIProxyAPI to v8.0.0 or later.
2. Replace the plugin binary with the new build.
3. Restart CLIProxyAPI.

The generic endpoints are `GET /v0/management/quota/providers` and
`POST /v0/management/quota/fetch`. Both require the management key.
Quota reset is unsupported. A management dashboard must support the generic
quota API to show plugin refresh controls. Backend support alone does not
add those controls to older dashboard builds.

Existing credential files keep their names and stable IDs. Set `name:` on an
API key to choose its display label. New files use readable names.

### Bug Fixes

- Preserve Claude image tool results when routing to Chat Completions: retain images in an associated user message after tool replies, preserve text, tool references and error markers, and include images in session derivation. The selected upstream model must support image input.

Special thanks to @c0mm4nd in #16.

- Fix `tool_result` translation when receiving `tool_reference` content blocks from Claude clients (such as ToolSearch, web search, or web fetch in Claude Code), preventing 500 errors and conversation lockouts on Chat Completions and Responses models like DeepSeek.

Special thanks to @SpiGAndromeda in #14.

## Upgrade Notes

- Replace the old plugin binary with the new release binary.
- Restart CLIProxyAPI after replacing the plugin.

**Upstream Changelog**: https://github.com/massiveits/opencode-go-cliproxyapi/compare/v0.2.1...v0.2.2
