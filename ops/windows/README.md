# SPEC #2 Windows deployment

`deploy_plugin.py <manifest.json>` is a local operations tool, not an unauthenticated web endpoint. Requires Python 3.11+, psutil, and administrator privileges in service mode. Credentials are read locally and never written to result receipts.

Prepare an exclusive transaction directory outside the install. Manifest explicitly pins candidate/installed SHA-256, exact destinations, verified backup filenames, initial running service order, local health port, selected auth name, baseline models and required OpenCode models. No in-place automatic discovery or service selection is performed during downtime.

The script verifies backups and baseline API health, launches a detached recovery watchdog, stops dependent services in reverse order, replaces files, starts previously running services and checks the baseline model list plus both selected-credential pre/post models. Failure restores original files and restarts services. `--recover` explicitly restores a transaction.

The service manifest is generated only after a live snapshot matches the LocalCPA dependency tree and exact WinSW executable/config binding; run the complete health and rollback rehearsals before production use. The checked-in `run-deployment.ps1` launcher runs the pinned Python interpreter elevated, writes launch/exit receipts, and must itself be independently detached from the chat process. Use a new protected transaction directory each time.

## 4. Current review status and stop condition
- Service-mode automation now records the recursive live LocalCPA dependency snapshot, validates the pinned WinSW executable/config, attempts all service starts, uses a per-transaction lock, and requires a detached watchdog readiness receipt before stopping.
- The current production OpenCode auth-files/models response is already empty. Record that exact pre-change fact; rollback must restore and verify that exact model set (currently empty), not imply that it can restore a working catalog that was absent before deployment. The existing live model list itself has core-owned models; it cannot prove OpenCode ownership. Candidate upgrade requires deepseek-v4.1-flash in the selected auth model list.
- The endpoint migration is a focused configuration override because production currently explicitly pins `/zen/go/v1`; isolated tests show both endpoints return the same model set and complete a DeepSeek Flash request.
- Elevated launch has been confirmed with a detached Start-Process probe. The remaining acceptance is a fresh protected production manifest, a separately reviewed final script, exact preflight, then guarded deployment. Do not treat the earlier unaudited process-mode rehearsal as production service acceptance.
- Keep the deployment process elevated and detached. If preflight, watchdog handshake, or service identity checks fail, make no service changes.
- After deployment, verify the live model menu, selected-auth model listing and an exact routed OpenCode request. Do not conflate an alias supplied by DeepInfra with OpenCode ownership.


Do not put manifests, configs, auth files, generated DLLs or transaction backups into Git. The service deployment must be launched elevated and detached from the chat/API caller. Preserve log/receipt files locally. Report `rolled_back` as a failed upgrade with restored service, not as successful installation.

Known CPA v8.0.15 limitation: plugin background catalog refresh does not refresh core per-auth registrations. This deployment checks for actual registered models; it does not fix that core mechanism.
