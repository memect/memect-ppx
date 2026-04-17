# Third-Party Licenses

This document tracks third-party code, assets, and bundled resources that
are included in or redistributed by this repository.

It is intended to support open-source release review and downstream
redistribution. The authoritative license terms remain the original license
files provided by upstream projects and asset authors.

## Included in This Repository

### PDF.js

- Path: `src/memect/web/libs/pdfjs`
- Upstream project: PDF.js
- Upstream authors: Mozilla and PDF.js contributors
- Repository status: vendored into this repository

Included upstream license files:

- `src/memect/web/libs/pdfjs/LICENSE`
- `src/memect/web/libs/pdfjs/web/cmaps/LICENSE`
- `src/memect/web/libs/pdfjs/web/standard_fonts/LICENSE_FOXIT`
- `src/memect/web/libs/pdfjs/web/standard_fonts/LICENSE_LIBERATION`

Release checklist:

- Preserve all upstream license files when redistributing.
- Record whether local modifications were made to vendored files.
- If modified, document the scope of modifications in this file or release notes.

### Bundled Font Assets

- Path: `src/memect/pdf/fonts`
- Repository status: bundled binary font assets

Files currently present:

- `src/memect/pdf/fonts/NotoSansSymbols2-Regular.ttf`
- `src/memect/pdf/fonts/wingdings.ttf`
- `src/memect/pdf/fonts/wingdings2.ttf`
- `src/memect/pdf/fonts/wingdings3.ttf`
- `src/memect/pdf/fonts/webdings.ttf`
- `src/memect/pdf/fonts/sans-serif/SourceHanSans-Bold.ttc`
- `src/memect/pdf/fonts/sans-serif/SourceHanSans-Regular.ttc`
- `src/memect/pdf/fonts/serif/SourceHanSerif-Bold.ttc`
- `src/memect/pdf/fonts/serif/SourceHanSerif-Regular.ttc`

Verified status by file group:

#### Fonts with repository-visible OFL-style license metadata

The following files contain license metadata indicating the SIL Open Font
License 1.1 or compatible redistribution-friendly terms in font metadata:

- `src/memect/pdf/fonts/NotoSansSymbols2-Regular.ttf`
- `src/memect/pdf/fonts/sans-serif/SourceHanSans-Bold.ttc`
- `src/memect/pdf/fonts/sans-serif/SourceHanSans-Regular.ttc`
- `src/memect/pdf/fonts/serif/SourceHanSerif-Bold.ttc`
- `src/memect/pdf/fonts/serif/SourceHanSerif-Regular.ttc`

Observed metadata summary:

- `Noto Sans Symbols 2`: license string references SIL Open Font License 1.1
- `Source Han Sans`: license string references SIL Open Font License 1.1
- `Source Han Serif`: license string references SIL Open Font License 1.1

Release note:

- These files are lower compliance risk than the symbol fonts below, but
  maintainers should still preserve or add explicit upstream license texts
  in the repository for complete redistribution hygiene.

#### Fonts that are currently high-risk for public redistribution

The following files contain Microsoft-origin font metadata and license text
that appears tied to the EULA of the product in which the font is included,
rather than a repository-friendly open-source redistribution grant:

- `src/memect/pdf/fonts/wingdings.ttf`
- `src/memect/pdf/fonts/wingdings2.ttf`
- `src/memect/pdf/fonts/wingdings3.ttf`
- `src/memect/pdf/fonts/webdings.ttf`

Observed metadata summary:

- `wingdings.ttf`: manufacturer metadata references Microsoft Typography
- `webdings.ttf`: manufacturer metadata references Microsoft Corporation
- license text found in metadata refers to use as permitted by the EULA of
  the product in which the font is included

Compliance assessment:

- These files should be treated as release blockers for a public GitHub
  source distribution unless maintainers can demonstrate redistribution
  rights that are compatible with the intended public release.
- The default safe action is to remove these font binaries from the public
  repository before release.
- If functionality depends on them, replace them with one of:
  a documented optional local-font lookup strategy
  a user-supplied font path
  an open redistributable substitute
  a code path that avoids bundling the binaries

Recommended release action:

- Remove `wingdings*.ttf` and `webdings.ttf` from the public repository.
- Update code and documentation so the project no longer requires shipping
  those font binaries inside the repository.
- Record the replacement approach in release notes or README.

Engineering impact summary:

- The repository contains explicit Wingdings handling logic in
  `src/memect/pdf/base.py` and `src/memect/pdf/wingdings.py`.
- The current codebase already includes conversion logic from Wingdings PUA
  characters to standard Unicode symbols.
- Documentation comments in `src/memect/pdf/base.py` and
  `src/memect/pdf/wingdings.py` indicate that standard Unicode output plus
  `NotoSansSymbols2-Regular.ttf` is an intended compatibility path.

Recommended remediation path:

- Make public builds rely on standard Unicode output by default.
- Treat Microsoft-origin symbol fonts as optional user-provided local fonts
  rather than bundled repository assets.
- If symbol-font recognition is still required, load fonts from an explicit
  user-supplied path or documented local system installation, not from
  committed binaries.
- Update any development utilities that assume bundled `wingdings*.ttf`
  files are present.

Recommended release-time additions:

- Add the upstream source URL for each font.
- Add the exact license name for each font.
- Add a note confirming redistribution is allowed.

## Referenced at Runtime But Not Necessarily Redistributed Here

### Python Dependencies

PPX depends on third-party Python packages declared in `pyproject.toml`.
These are generally installed from package indexes rather than committed into
this repository, but they still carry their own license obligations.

Review areas:

- direct dependencies
- transitive dependencies
- native library dependencies
- platform-specific runtime packages

Recommended release-time action:

- Generate a dependency license inventory for published wheels and runtime environments.

### Model Weights and External Services

PPX can integrate with external OCR and LLM backends. Model weights, service
images, or hosted endpoints may have separate commercial terms, model
licenses, usage restrictions, or redistribution limits.

Release checklist:

- Do not assume model or service availability implies redistribution rights.
- Document any bundled model assets separately if they are ever added to this repository.
- Document any required third-party service terms in the public docs.

### Sample Documents and Test Fixtures

If sample PDFs, images, or golden outputs are added later, they must be
reviewed separately for copyright and redistribution rights.

Release checklist:

- Only include files that are clearly safe to redistribute.
- Avoid customer, partner, internal, or proprietary documents.
- Prefer synthetic or explicitly licensed public samples.

## Relationship to NOTICE

- `NOTICE` provides a short attribution-oriented overview.
- This file provides the detailed third-party review checklist and inventory.

Both files should be kept in sync as the repository evolves.

## Maintainer Follow-Up

Before public GitHub release, complete the remaining verification for:

- exact font licenses
- whether vendored assets were modified locally
- whether future test data is safe to redistribute
- whether published distribution artifacts bundle anything not listed here
