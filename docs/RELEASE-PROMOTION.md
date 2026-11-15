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

Immediately after confirmation, the Releases panel shows the publication request
as starting and prevents another promotion for that project while it runs. Progress
and errors appear in the same panel and survive navigation through the existing
owner-action history. An accepted Git-provider request explicitly states that the
provider workflow still needs to finish; it is not reported as a published release.
