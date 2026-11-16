# Release updates

HaiveQueen checks GitHub for releases every 24 hours by default and when you select **Check now** or run `bzz update`. Set `update.checkHours` in `config.toml` to change the interval; `0` or `BZZ_NO_UPDATE_CHECK=1` disables automatic checks. A failed check is shown in the dashboard and can be retried. Releases come from the configured `update.repository = "owner/name"`, or the installation's GitHub origin when it is not set.

When a release is offered, select **Update** and confirm, or run `bzz update --install X` for the offered version X. A release is installed under the installation root in its own `versions/<version>/` folder, alongside the previous version. The database, settings, profiles and run logs remain in the shared data folder. The fixed `bzz` and start launchers at the installation root follow the `active-version` pointer; shortcuts and commands continue to point to the same place. HaiveQueen removes an old version when it is no longer used, retaining it when use cannot be ruled out.

For a compatible release, the core switches to the new version without waiting for running Queens, Agents, Builds or Deploys. They continue with their original version; Agents started by an existing Queen also use her version. The next Queen uses the new version. The dashboard shows the core version, each active plan's Queen version and the version of running Build and Deploy jobs. If the new core fails its first round, HaiveQueen restores the previous version and reports the rollback.

## Incompatible releases: wait or force

A release whose shared changes cannot safely coexist with running older versions waits for those jobs. New work stops starting while existing Queens, Agents, Builds and Deploys finish. The dashboard update area shows that it is waiting; hover over it to see the reason and running jobs. Choosing to wait needs no further action: the update and restart proceed when the jobs finish. `bzz update` reports the wait, reason and jobs without forcing.

While the update waits, the dashboard and the Windows tray offer force. They warn that all running work will stop immediately and unfinished work will be repeated; force proceeds only after confirmation. On the command line, `bzz update --force` prints the warning and its explicit flag is the confirmation. After restart, plans continue from their recorded state.

If publication is rejected after the incompatible-update wait, the update remains failed. HaiveQueen verifies a completed round of the previous core before restoring scheduling. Missing recovery evidence keeps scheduling paused and is reported explicitly. An existing owner pause or stop is preserved. Automatic core restarts do not open browser or setup windows.

The older checkout-only `bzz self-update` is a one-time transition and is retired once a release is published or the checkout is at a release tag. It is separate from the release workflow above.
