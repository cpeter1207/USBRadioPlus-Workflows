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

Automatic quality and release work runs on native Debian 13 only: amd64
collects coverage, while amd64 and arm64 both run tests, builds, and staged
installation checks. Debian 12 is aspirational and never runs automatically.
The reusable package workflow accepts `include_debian12: true` only for an
explicit manual Debian 12 package build.
