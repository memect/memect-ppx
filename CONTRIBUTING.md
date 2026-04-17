# Contributing to PPX

Thank you for your interest in PPX! We welcome contributions of all kinds — code, bug reports, feature suggestions, and documentation improvements.

## Types of Contributions

| Type | Description |
|------|-------------|
| **Bug fixes** | Fix parsing errors, compatibility issues, crashes, etc. |
| **New features** | Add new backends, output formats, CLI commands, etc. |
| **Performance** | Improve parsing speed or reduce memory usage |
| **Documentation** | Improve README, examples, docstrings, or API docs |
| **Tests** | Add test cases or improve coverage |

---

## Questions & Feedback

Before opening an issue, please:

1. Check the [README](./README.md) and existing [Issues](https://github.com/memect-hq/ppx/issues) to avoid duplicates
2. Confirm the problem is reproducible on the latest version

**Bug reports** should include:
- Steps to reproduce (minimal reproducible example)
- Expected behavior vs. actual behavior
- Environment info (OS, Python version, PPX version)
- Relevant logs or screenshots

**Feature requests** should describe:
- The specific problem or use case it addresses
- Your preferred interface or API shape (if any)

---

## Contributing Code

### Prerequisites

| Tool | Version |
|------|---------|
| Python | >= 3.12 |
| [uv](https://docs.astral.sh/uv/) | latest |

### Workflow

```bash
# 1. Fork this repo, then clone your fork
git clone https://github.com/<your-username>/ppx.git
cd ppx

# 2. Install dependencies
uv sync

# 3. Create a branch
git checkout -b feat/my-feature
# or
git checkout -b fix/issue-123

# 4. Make your changes

# 5. Run linting
uv run ruff check src/
uv run ruff format --check src/

# 6. Push and open a Pull Request against main
git push origin feat/my-feature
```

### Testing

Before submitting a PR, make sure your changes:

- Do not break existing functionality (run `ppx parse` on a typical PDF to verify)
- Include test cases or examples for any new functionality
- If new parsing logic is involved, consider adding an output sample under `output/`

---

## Code Style

- **Formatter / linter**: [Ruff](https://docs.astral.sh/ruff/) — run it before committing
- **Comments**: Match the language already used in each file
- **Focused PRs**: Keep each PR to a single goal; avoid mixing unrelated changes
- **YAGNI**: Only implement what is needed now — avoid over-engineering

---

## Commit Message Format

```
<type> <short description>
```

Use one of the following types:

| Type | When to use |
|------|-------------|
| `Add` | New feature, file, or dependency |
| `Fix` | Bug fix |
| `Update` | Improvement to existing functionality |
| `Refactor` | Code restructure with no behavior change |
| `Docs` | Documentation-only change |
| `Chore` | Build, tooling, or config change |

**Examples:**
```
Add support for GLM-OCR backend
Fix table parsing crash on rotated PDF pages
Update CLI help text for parse command
```

---

## Contributor License Agreement (CLA)

When opening your first PR, please read and agree to [PPX_CLA.md](./PPX_CLA.md) by including the following statement in your PR description:

```
I have read and agree to the PPX Contributor License Agreement.
```

---

## Pull Request Review

- Maintainers will respond within **5 business days**
- Keep your branch up to date with `main` to avoid merge conflicts
- After addressing review feedback, leave a brief comment explaining your changes

---

## License

PPX is licensed under [LGPL-3.0](./LICENSE).  
By submitting a contribution, you agree to license your work under the same terms.
