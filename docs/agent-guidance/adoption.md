# Guidance adoption record

This record supports guidance adoption and refresh. Routine development uses
[AGENTS.md](../../AGENTS.md) and [README.md](../../README.md).

## Source and baseline

- Source library `C:\dev\agent-guidance`, upstream
  [tunnelvisionlabs/agent-guidance](https://github.com/tunnelvisionlabs/agent-guidance).
- Refreshed on 2026-11-23 from source revision
  `80daa7a03b1ecbbc2ff7e56430a15fc9b61a7651` (add GitHub Markdown guidance).
  The library working tree and index were clean. Both exact source revisions
  were available locally.
- The previous refresh on 2026-11-17 adopted source
  `7e057ff583ff07e7117ea3b81e8591dc65ef5175`. Its full record is preserved in
  destination commit `78ffd66`. The first adoption on 2026-11-14 used source
  `0d354b6b503e69a9c58776166e6de19e0281a1bc` and landed in destination commit
  `a2412c1dacab513fdd34460e7b79fb58cf9abe0f`.
- Destination baseline `e41f152e43ff3169ff1ad2cb8a067a9e9997fa7d` had a clean
  working tree and index. `AGENTS.md` and this record matched the previous
  refresh. Subsequent development reorganized current guides and documented
  API contracts, and added one-second polling. The project remains one
  Python/Home Assistant integration with GitHub Issues for planning.
- Reviewed `docs/adopting.md`, the catalog, `skills/adopt-guidance/SKILL.md`,
  and the adoption record template. The procedure, template, and both complete
  skill folders are unchanged since the previous source. The catalog adds
  GitHub Flavored Markdown and updates the Windows recovery title. The GitHub
  planning module adds a Markdown cross-reference. Windows recovery now
  calls for at least one actual rebuild or relink on a matching failure and
  uses diagnostics to decide whether to try again.
- Reconciled the exact previous and current source revisions with the current
  repository instructions. Rechecked the applicability, intended policy,
  enforcement, and current practice of earlier selections and exclusions,
  including unchanged modules.

## Final module mappings

Paths in the source column are relative to the library. Destinations are
sections of `AGENTS.md` unless otherwise stated. Shared scope applies only
to this project.

| Module ID | Source path | Scope and destination | Adaptation and reason |
| --- | --- | --- | --- |
| shared.defaults | `guidance/shared/defaults.md` | Shared · Project and working conventions, Validation commands and tooling | Keep the existing Python/HA paths, project idioms, verified commands, proportionate checks, and protection for unrelated work. |
| shared.documentation | `guidance/shared/documentation.md` | Shared · Documentation and API contracts, GitHub issues and stories | Keep reader tasks and independent entry points, qualifications at the claim, accurate applicability, proportionate detail, and checks for both whole-piece and skimmed conclusions. |
| shared.documentation-lifecycle | `guidance/shared/documentation-lifecycle.md` | Shared · Documentation and API contracts, GitHub issues and stories | Keep code and contracts, current guides, Issues, and history as the primary sources for different needs. Restore explicit authority, version, and transition checks. Retain both readiness conditions and the repository's hardware evidence limits. |
| shared.api-contracts | `guidance/shared/api-contracts.md` | Shared · Documentation and API contracts | Keep native Python docstrings for new or touched reusable APIs, shared types, meaningful shared members, and requested documentation work. Preserve caller-visible conditions, failures, results, and side effects. Restore execution context beyond the event loop and references to workaround evidence. |
| shared.sensitive-data | `guidance/shared/sensitive-data.md` | Shared · Sensitive data and live systems | Preserve rules on private identities and captures, client-visible environments, fictional fixtures, ignored storage, and package review. Restore purpose-based retention and preservation of prior physical evidence. |
| shared.live-system-access | `guidance/shared/live-system-access.md` | Shared · Sensitive data and live systems | Apply to live Home Assistant, valve and firmware operations, provisioning, deployment, signing, and publication. Make explicit authorization clear while preserving identity, integrity, uncertain-write reconciliation, and evidence boundaries. |
| strategies.behavior-testing | `guidance/strategies/behavior-testing.md` | Shared · Testing, GitHub issues and stories, Validation commands and tooling | Preserve production paths, faithful fakes, meaningful assertions, evidence-based isolation, and proportional verification. Restore same-change regression coverage as a direct testing rule. |
| languages.home-assistant-testing | `guidance/languages/home-assistant-testing.md` | Shared · Testing, Validation commands and tooling | Preserve the original HA testing block from `9fc5db85809b586d75eb5c7294ba868ebfe5d601`, including real HA objects, observable transitions, timing and ownership, distinct old/replacement data, and the live hardware evidence boundary. |
| strategies.incremental-delivery | `guidance/strategies/incremental-delivery.md` | Shared · Project and working conventions, GitHub issues and stories | Keep reviewable, loadable steps, real dependencies, first consumers, same-step tests, and explicit development and deployment platform selection. The CI matrix covers platform targets without selecting a deployment. |
| strategies.github-planning | `guidance/strategies/github-planning.md` | Shared · GitHub issues and stories, GitHub Markdown | Preserve local issue content and relationships, native type discovery and verification, saved-body readback, and hardware/merge acceptance. Keep issue-specific detail placement under planning and put shared rendering rules in GitHub Markdown. |
| strategies.reproducible-verification | `guidance/strategies/reproducible-verification.md` | Shared · Project and working conventions, Validation commands and tooling | Preserve Python/pip/pytest workflows, separate HA targets, plugin and socket isolation, conditional formatting, index safety, generator checks, and distinctions between validation stages. Include SDKs and build assets in the first-use pin rule when introduced. |
| languages.github-flavored-markdown | `guidance/languages/github-flavored-markdown.md` | Shared · GitHub Markdown | Adopt wrapping by destination, reference and citation rules, headings and accessibility, lists, fences, tables, GitHub features, and rendering checks. Use existing file wrapping and tooling policy. The source examples are illustrative, and the adopted rules are self-contained. |
| personal.git | `guidance/personal/git.md` | Personal source audience, explicitly shared · Git authorization and message construction, Commit message guidelines, GitHub issue closure | Preserve separate staging and commit authorization, closure rules, explicit paths, actual-newline construction, stored-message verification, and the fenced draft. Project commit-message rules take precedence over source fallback defaults. |
| personal.writing | `guidance/personal/writing.md` | Personal source audience, explicitly shared · Writing and review presentation, GitHub issues and stories | Preserve the Oxford comma, concise PR titles and final-diff alignment, full drafts or labeled excerpts, and stable alternatives. The local draft format and review practice cover the source requirements. |

## Skills

| Skill | Source path | Use and installation decision |
| --- | --- | --- |
| adopt-guidance | `skills/adopt-guidance/SKILL.md` at the current source revision | Used directly for this requested refresh. Outside the active repository workflow. |
| docpass | `skills/docpass/SKILL.md` at the current source revision | Reconsidered after the API documentation pass in destination commit `5e6fbe5`. That change demonstrates API documentation work, and the standing API contract rules already apply. No recurring repository workflow calls for installing the skill. It remains available for a future requested API documentation review. |

Both source skill folders and the skill catalog are unchanged. No skill files
or settings were added to the destination.

## Restorations and retained decisions

| Source requirement | Prior treatment and current resolution |
| --- | --- |
| Documentation authority and transitions | The earlier guide named primary sources but left placement, references, applicable versions, and transition review partly implicit. It now requires an identifiable primary home, links that convey authority and temporal scope, and an explicit review of what stays, changes, or leaves active guides. Preserve discoverable future scope, applicable breaking-change advice, and outstanding deployment or hardware evidence. The current README, polling guide, and documentation history support this division of ownership. |
| Caller execution context and workaround references | The API bullet named only the event loop, and the workaround bullet did not clearly require a reference. The guide now covers relevant execution contexts, including worker threads, and calls for an evidence reference with the affected version and failure. Current connection and callback code makes execution context relevant, and the protocol guides distinguish observation from inference. |
| Regression coverage with behavior | The same-step requirement appeared under GitHub planning. Testing now also states it directly so it applies outside a planned story. The existing exception for documentation-only and reversible, low-impact edits still applies to tests that would merely restate them. |
| Conditional pinning and purpose-based retention | The guide previously summarized these as tool/component pins and preservation of intentional evidence. It now names SDKs and build assets where introduced, ties retention to purpose, and explicitly preserves earlier physical evidence. Existing pins and private-storage rules remain the local implementation. |
| Explicit live authorization | Prior wording referred to relevant authorization without always naming its explicit nature. The guide now calls for explicit authorization for live changes and reuses valid authorization within its scope, consistent with the repository's external-write constraints. |
| Earlier restorations | Retained the previous refresh's pre-/post-MVP conditions, authority handoff, documentation coverage and authoring details, test and fixture rules, validation sequencing, native issue-type fallback, and sensitive/live-system cases. A missing formatter or coverage checker describes enforcement. The standing requirements still apply. |

- The user explicitly chose shared project scope for both personal modules
  on 2026-11-14. Both modules are unchanged and remain applicable with the
  same scope. There are no new personal rules or unresolved material policy
  conflicts. The project-specific guidance from `d0a6e644` still supports
  paragraphs and non-obvious reproduction instructions in commit messages.
  Routine successful checks belong in the accompanying response. The README
  initialization prompt leaves commit-draft presentation to `AGENTS.md`.
- The HA testing block and `tests/conftest.py` support real Home Assistant
  fixtures and evidence-based isolation. The upstream plugin exclusion has a
  concrete Windows `fcntl` import reason. Keep simulated Bluetooth I/O,
  temporary storage, and external socket restrictions. Current validation
  configuration supports Python 3.14, HA 2026.8.0 and 2026.11.1, and the
  Ubuntu/Windows CI matrix. Deployment selection remains task-specific.
- Current Markdown uses hard-wrapped paragraphs, tables, relative links, and
  fenced examples. GitHub issue instructions use collapsed details. These
  conventions support adopting single-line prose for GitHub conversations
  while retaining file wrapping and the separate commit-message convention.
  No Markdown formatter, linter, or link checker is configured here.
- A local, untracked `local-artifacts/.gitignore` wildcard covers private
  evidence. Verify coverage in each checkout before writing such material.
  Keep historical protocol or hardware evidence where it supports current
  interpretation or acceptance. GitHub Issues retains future scope and
  unresolved acceptance.

## Retained exclusions

- `strategies.windows-test-recovery` still requires a confirmed Windows
  Application Control or Code Integrity rejection of a locally built test
  executable from the current work. This Python/pytest workflow builds no
  test executable, and no matching rejection was found. The revised retry
  policy does not apply to an interpreter or import failure. Re-evaluate
  the trigger if a matching workflow and failure arise.
- C#/.NET, TypeScript/JavaScript API documentation, C/ESP-IDF, Solid and UI
  architecture, media sessions, embedded firmware, geometry, printing, and
  model-release guidance remain outside this project's technologies and work.
  The tracked files, manifests, and CI still describe a Python integration.
  Valve and proxy communication does not bring their firmware into scope.
  Examples that depend on those ecosystems do not replace the applicable
  general requirements.
- Model-training and held-out evaluation rules do not apply to valve records
  or protocol fixtures. Test expectations still need an independent source.
  General privacy, authorization, and evidence rules cover applicable cases
  without introducing printing or slicing procedures.
- Kept provenance and source paths here, outside automatically loaded
  instructions. The active guide works from the destination checkout and
  requires no library synchronization, installed adoption skill, or routine
  reading of this record.

## Verification

- Compared both source revisions and their catalogs, procedures, templates,
  complete skills, selected module text, and changed strategy guidance.
  Reviewed repository history, instructions, README and maintained guides,
  tracked files, manifests, requirements, CI, and fixture configuration.
- Reviewed both edited documents for source coverage and consistency. Local
  `ConvertFrom-Markdown` in PowerShell 7.6.9 rendered the headings, tables, and
  inline examples. Checked local links, the pinned test targets and fixture
  settings, and private evidence ignore coverage. Rendering was checked
  locally, without publishing a GitHub preview.
- `git diff --check` passed. The Git index's SHA-256 matched the initial
  snapshot, and status showed only unstaged changes to `AGENTS.md` and this
  record. Application and hardware tests were outside this documentation-only
  verification.
