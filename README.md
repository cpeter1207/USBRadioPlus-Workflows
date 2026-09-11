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

Automatic quality and release work runs on native Debian 13 only: amd64
collects coverage, while amd64 and arm64 both run tests, builds, and staged
installation checks. Debian 12 is aspirational and never runs automatically.
The reusable package workflow accepts `include_debian12: true` only for an
explicit manual Debian 12 package build.
