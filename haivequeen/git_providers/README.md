# Git provider module contract

Add a Python module in this directory. Export `CAPABILITIES`, a collection of
public operation names, and `detect(remote)`. Return `None` for an unsupported
Git remote, otherwise return the repository identifier passed as the first
argument to each operation. Export a callable for each advertised operation.
The caller supplies the remaining arguments. Advertise only implemented
operations; consumers should check capabilities before calling them.

`git_provider.discover` returns the matching provider and a list of errors.
A broken import, contract or detector is reported by module name and does not
prevent discovery of other modules. `Provider.call` reports operation failures
as `ProviderError`. Modules should validate their own inputs and avoid doing
network or authentication work during detection. Multiple matches are reported;
the first module in alphabetical order wins.
