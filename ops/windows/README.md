# SPEC #2 Windows deployment

`deploy_plugin.py <manifest.json>` is a local operations tool, not an unauthenticated web endpoint. Requires Python 3.11+, psutil, and administrator privileges in service mode. Credentials are read locally and never written to result receipts.

Prepare an exclusive transaction directory outside the install. Manifest explicitly pins candidate/installed SHA-256, exact destinations, verified backup filenames, initial running service order, local health port, selected auth name, baseline models and required OpenCode models. No in-place automatic discovery or service selection is performed during downtime.

The script verifies backups and baseline API health, launches a detached recovery watchdog, stops dependent services in reverse order, replaces files, starts previously running services and checks both the baseline model list and per-auth OpenCode models. Failure restores original files and restarts services. `--recover` explicitly restores a completed transaction. Do not reuse a transaction directory.

The watchdog uses PID plus creation-time identity and an OS file lock, and restores if the deployment owner dies. It never restores files underneath a live deployment owner. This is not a guarantee against OS failure, power loss or a permanently hung Windows SCM; recoverability is verified through bounded normal-failure and owner-death rehearsals before production use.

Do not put manifests, configs, auth files, generated DLLs or transaction backups into Git. The service deployment must be launched elevated and detached from the chat/API caller. Preserve log/receipt files locally. Report `rolled_back` as a failed upgrade with restored service, not as successful installation.

Known CPA v8.0.15 limitation: plugin background catalog refresh does not refresh core per-auth registrations. This deployment checks for actual registered models; it does not fix that core mechanism.
