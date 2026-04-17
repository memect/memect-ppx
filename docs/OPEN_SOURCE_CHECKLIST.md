# Open Source Release Checklist

This checklist is for preparing PPX for a public GitHub release. It focuses on the work still missing before the repository should be treated as a maintainable open-source project.

## P0 Must Finish Before Going Public

- Add community governance files:
  `CODE_OF_CONDUCT.md`
  `SECURITY.md`
  `.github/pull_request_template.md`
  `CODEOWNERS`
- Unify all public repository references across docs:
  GitHub org/repo
  issue tracker URL
  PyPI URL
  future docs URL
- Fix the CLA file naming mismatch:
  `CONTRIBUTING.md` references `PPX_CLA.md`
  `.gitignore` currently whitelists `MS_CLA.md`
  Keep one name and make docs, ignore rules, and repository contents consistent.
- Remove internal environment residue from public-facing files:
  internal image naming such as `x2x-*`
  internal-style wording such as "intranet" or company-specific conventions
  default mirror choices that are too environment-specific unless clearly documented
- Build a minimum public test suite:
  create `tests/`
  add one CLI smoke test
  add one PDF parse test
  add one image parse test
  add one output-structure assertion test
  add one invalid-input test
- Upgrade CI from install-only checks to contributor-safe gates:
  lint on push and pull request
  tests on push and pull request
  build on push and pull request
  release as a separate workflow
- Complete package metadata in `pyproject.toml`:
  `authors`
  `license` declaration
  `classifiers`
  `project.urls`
  `keywords`
- Audit bundled third-party assets and document license boundaries:
  vendored `pdfjs`
  bundled fonts
  any redistributed models or resources
  Add `NOTICE` or `THIRD_PARTY_LICENSES.md`.
- Remove or replace high-risk bundled fonts before public release:
  `wingdings.ttf`
  `wingdings2.ttf`
  `wingdings3.ttf`
  `webdings.ttf`
  Their embedded metadata indicates Microsoft-origin EULA-style usage terms,
  which should not be assumed redistributable in a public source repository.

## P1 Strongly Recommended Before Release

- Rewrite `.gitignore` into a normal maintainable structure instead of using root-level deny-all plus selective allow rules.
- Expand `README.md` and `README_zh-CN.md` with:
  CPU install path
  macOS and Linux install notes
  CUDA install path
  minimal sample input/output
  supported and unsupported cases
  performance and resource expectations
  troubleshooting notes
- Remove placeholder claims that are not ready yet, such as benchmark sections that still say "coming soon".
- Define a public sample-data policy in `CONTRIBUTING.md`:
  public fixtures only
  no customer documents
  no internal documents
  no copyrighted documents without redistribution rights
- Decide whether maintainer-only files should stay public, be documented, or be removed:
  `uv.toml`
  `uv.toml.example`
  `scripts/release_github.sh`
- Document server security expectations:
  default `0.0.0.0`
  permissive CORS
  local-only recommendation by default
  production deployment hardening requirements

## P2 Good Follow-Up Work After Release

- Add `CHANGELOG.md`.
- Add a roadmap or project-status document.
- Add runnable examples under `examples/`.
- In the next release, complete contributor-facing quality gates:
  add a minimum public test suite
  document stable test commands
  document lint/format commands
  align CONTRIBUTING.md with the actual local development workflow
- Add standard contributor tooling:
  `pytest`
  coverage
  `pre-commit`
  stricter lint/format configuration
  optional type checking
- Configure GitHub repository settings after publication:
  description
  topics
  homepage
  branch protection
  required status checks
  default labels

## Suggested Output Files

- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `.github/pull_request_template.md`
- `CODEOWNERS`
- `NOTICE` or `THIRD_PARTY_LICENSES.md`
- `CHANGELOG.md`
- `RELEASE_BRANCH_FLOW.md`
- `tests/`
- `tests/fixtures/`
- `tests/golden/`

## Recommended Execution Order

1. Unify the public identity of the project:
   repository URLs, CLA naming, maintainer-facing metadata, license presentation.
2. Remove internal residue:
   image naming, environment-specific defaults, internal wording, maintainer-only script assumptions.
3. Add the minimum test and CI safety net.
4. Expand public documentation for install, usage, contribution, and license clarity.
5. Do one clean-room validation on a fresh machine:
   install from scratch, run the README example, run tests, build package artifacts.

## Ready-to-Publish Standard

PPX is ready to go public when all of the following are true:

- A new external user can install it and complete the README quick start.
- A new contributor can understand how to open issues and submit pull requests.
- License boundaries for PPX and bundled third-party assets are clear.
- CI blocks obvious regressions before changes merge.
- The repository no longer exposes obvious internal-only engineering residue.
