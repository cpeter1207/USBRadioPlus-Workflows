# USBRadioPlus Workflows

Reusable GitHub Actions workflow implementations for
[USBRadioPlus](https://github.com/cpeter1207/USBRadioPlus).

The USBRadioPlus repository owns event triggers, permissions, secrets, and
references to this repository's protected `main` branch. This repository owns the implementation of its
quality, platform-test, package, container, documentation, site, and release
automation.

Changes here validate workflow structure without building USBRadioPlus. Once a
change reaches `main`, subsequent caller runs use it without changing the
USBRadioPlus repository.

Ordinary production pushes invoke the reusable fast preflight, which runs only
formatting, lint, and static analysis. Pull requests invoke the reusable full
quality gate, including Doxygen and the native Debian 13 test matrix, and the
full gate is the required merge check. Releases use a main revision that has
already passed that pull-request gate and run artifact-specific validation
without repeating it. API documentation publishes separately when a validated
change reaches `main`. Release metadata and the following development-version bump are
ordinary, separately validated pull requests; release automation does not make
or merge bookkeeping commits.

USBRadioPlus builds three Debian binary packages: `usbradioplus` contains the
Asterisk integration and tuner, `libusbradioplus-product1` contains the shared
radio product and AGC plugin, and `libusbradioplus-product-dev` contains its
public header, linker symlink, and pkg-config metadata. The integration and
development packages depend on the exact matching product runtime; the product
runtime depends on the lower shared providers, not Asterisk or the controller.
All three outputs are staged for the existing signed APT publisher.

The integration is built against ASL3 3.9.3. On each native Debian 13 architecture the pull-request gate loads
the identical package under ASL3 3.9.3 and 3.10.5 and verifies the module hash
does not change. Those exact runtime alternatives are recorded in the package;
there is no legacy/modern package selection or resource-module header staging.
Both release workflows verify the merged source's completed pull-request gates
before building artifacts, and never repeat the production coverage gate.

Shared adapters are installed from versioned releases after checking GitHub's
SHA-256 asset digests. The module uses ring ABI 3 from v3.0.0-alpha2 and
sample-rate adapter ABI 2 from v0.2.0-alpha1. Its radio ABI 4 and GPIO ABI 1
dependencies are pinned to v0.1.0-alpha.8 and v0.1.0-alpha.4. Radio's squelch
and CTCSS providers use ABI 2 (`librptadvsquelch2` and `librptadvctcss2`,
SONAME `.so.2`) from v0.1.0-alpha.2; DCS and signaling remain ABI 1 from
v0.1.0-alpha.1. Existing ABI 1 index entries remain published.
Container builds receive these same released packages as the `shared_packages`
build context. Each library's independent repository owns its implementation,
tests, packaging, and release gate. Publish those exact owner and provider
releases before activating these consumer pins or rebuilding quality images.

After a ring or sample-rate-adapter release succeeds, its thin source caller
passes `APT_INDEX_DISPATCH_TOKEN` to the reusable release workflow. The token
must be a fine-grained PAT with only `Contents: write` on
`cpeter1207/USBRadioPlus`; store it as that secret in both source repositories.
The workflow dispatches the released tag to USBRadioPlus, whose existing
package publisher downloads those exact release assets and refreshes the signed
Debian 13 index without rebuilding USBRadioPlus or its candidate dependency pins.
A ring refresh includes the explicitly paired sample-rate adapter release;
a standalone adapter refresh includes only that adapter's packages.

For a released functional library, the same publisher downloads only that
library's digest-verified packages and refreshes the signed Debian 13 index
without building new USBRadioPlus integration or product packages. Other
dependencies and existing consumer packages remain in the preserved repository
pool; the published-repository verification job still checks the consumers.
Radio-core and GPIO adapter owner releases use this same index-only route.

Automatic quality and release work runs on native Debian 13 only: amd64
collects coverage, while amd64 and arm64 both run tests, builds, and staged
installation checks. Debian 12 is aspirational and never runs automatically.
The reusable package workflow accepts `include_debian12: true` only for an
explicit manual Debian 12 package build.
