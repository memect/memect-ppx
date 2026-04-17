# Release Branch Flow

This document defines the branch responsibilities and release flow used for
the PPX open-source publishing process.

Official GitHub repository:

- <https://github.com/memect/memect-ppx>

## Branch Roles

### `main`

Purpose:

- Internal development branch
- Used for ongoing engineering work
- May contain internal-only process files, temporary assets, and work-in-progress changes

Rules:

- `main` is not treated as the public open-source release branch
- Changes on `main` do not need to be immediately ready for public GitHub release
- Internal workflow convenience takes priority on this branch

### `dev/open`

Purpose:

- Open-source preparation branch
- Used to transform internal development output into publishable open-source content
- Used for documentation cleanup, license review, asset review, release copywriting, and repository hygiene work

Typical work on this branch:

- clean up repository content for public release
- remove or exclude internal-only files
- update README and release-facing docs
- review third-party assets and license boundaries
- prepare benchmark documentation and references
- prepare examples, release materials, and public-facing assets

Rules:

- `dev/open` may contain release-preparation materials that are not part of the final public release
- This branch is a staging area for open-source cleanup, not the final public snapshot

### `gitlab-release`

Purpose:

- Final release branch
- Source branch used for the final public release push to GitHub
- Must contain only content intended for public open-source publication

Rules:

- `gitlab-release` should stay clean and release-ready
- Do not treat `gitlab-release` as an internal working branch
- Do not keep internal-only process files on this branch
- Only content safe for public GitHub publication should remain here

## Merge Direction

Recommended merge direction:

```text
main -> dev/open -> gitlab-release -> GitHub
```

Meaning:

1. Internal development is done on `main`
2. Public-release cleanup and preparation are done on `dev/open`
3. Final release content is merged into `gitlab-release`
4. `gitlab-release` is pushed to the official GitHub repository

## Release Procedure

### Step 1. Continue internal development on `main`

Use `main` for day-to-day engineering work.

Examples:

- feature development
- refactoring
- internal process scripts
- temporary release preparation files
- experimental or unfinished release materials

### Step 2. Sync `main` into `dev/open`

When preparing an open-source release, merge the latest internal work into
`dev/open`.

Example:

```bash
git checkout dev/open
git merge main
```

Use `dev/open` to complete all open-source preparation work.

### Step 3. Clean and finalize on `dev/open`

Before promoting content to `gitlab-release`, complete the release-preparation
tasks on `dev/open`.

Typical checklist:

- confirm repository URLs are correct
- verify README and docs are public-ready
- remove internal-only process files
- confirm example assets are safe to publish
- confirm bundled third-party assets are reviewed
- confirm contribution and license documents are aligned
- confirm release copy and public docs are finalized

### Step 4. Merge `dev/open` into `gitlab-release`

After the branch is ready for publication:

```bash
git checkout gitlab-release
git merge dev/open
```

This merge should produce the final public snapshot.

### Step 5. Final release review on `gitlab-release`

Before pushing to GitHub, review the final branch carefully.

Minimum expectations:

- only public-facing files remain
- no internal-only process files are included
- no temporary artifacts are included by mistake
- release docs are complete
- benchmark docs, citations, and attributions are present where needed
- third-party notices and license boundaries are documented
- repository links point to the official GitHub repository

### Step 6. Push `gitlab-release` to GitHub

The public GitHub release should be published from `gitlab-release`.

This is the official GitHub repository:

- <https://github.com/memect/memect-ppx>

## Rules for Process Files

This repository may contain many release-preparation files and process artifacts
during work on `main` or `dev/open`.

Rules:

- process files may exist on `main`
- release-preparation files may temporarily exist on `dev/open`
- such files should not remain on `gitlab-release` unless they are intentionally public

In other words:

- `main` can optimize for internal work
- `dev/open` can optimize for release preparation
- `gitlab-release` must optimize for public publication quality

## Public Release Standard

`gitlab-release` should be considered ready for GitHub only when:

- the repository content is safe for public release
- public documentation is complete
- license boundaries are clear
- example assets are safe to redistribute
- open-source governance files are in place
- there are no obvious internal-only leftovers

## Operational Guidance

Recommended branch discipline:

- do not push `main` directly to GitHub as the public release branch
- do not use `gitlab-release` for ordinary internal iteration
- do not merge `gitlab-release` back into `main` unless there is a specific and intentional need
- keep the public release history focused and understandable

## Summary

The PPX release flow is:

```text
main
  -> dev/open
      -> gitlab-release
          -> GitHub (https://github.com/memect/memect-ppx)
```

This flow separates:

- internal development
- open-source preparation
- final public release

That separation reduces accidental disclosure, keeps release quality higher,
and makes the GitHub publication process easier to manage.
