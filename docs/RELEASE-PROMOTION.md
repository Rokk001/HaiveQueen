# Release promotion and feedback

Promoting an Alpha or other prerelease requires explicit confirmation. When the
full-version tag already exists as a lightweight maintenance tag at the exact
selected commit, the GitHub adapter leaves it unchanged and dispatches
`.github/workflows/release.yml` on the repository default branch with
`release_tag` and `source_tag` inputs. The repository must support this dispatch
contract and the credential must permit workflow dispatch. Refusals are shown
as errors. A conflicting commit is rejected; tags are never moved or deleted.

The release workflow accepts explicit promotion independently of maintenance-tag
pushes. It validates the version names, equal source/target commits and the source
prerelease status, then copies the existing distributions without rebuilding.
Ordinary maintenance pushes still do not publish releases. Full-release operations
for the same tag are serialized without cancelling a running publication.

Immediately after confirmation, the selected prerelease card changes its badge
and disabled button to `Publishing…`. Acceptance of a provider request does not
mean publication has finished. The UI checks the saved request every ten seconds,
including after navigation or reload, until publication or workflow failure is
confirmed. The GitHub workflow title identifies the source and target versions.
A full release must have the requested commit and the same asset names and digests
as its prerelease before it is reported as published. The alpha card then shows
`Promoted to v…` and `View full release`, linking to the separate stable card.

A failed publication shows `Publication failed`, the error and a `Retry` button.
A retry dispatches the workflow again without moving the existing tag. Temporary
verification connection errors keep publication pending and never resubmit it.
Completed historical request errors remain under previous owner actions; saved
provider requests awaiting confirmation are restored and verified after reload.

The update banner also changes immediately after confirmation: it first shows
`Starting update to v…`, then the installation/waiting state. Install and check
buttons remain unavailable during installation. Request errors appear in the
banner. Status refresh runs on every page, and responses started before a new
installation cannot overwrite its immediate feedback. Installation success and
failure still come from the existing update worker state; the UI does not infer
success from accepting the request.
