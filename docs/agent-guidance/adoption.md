# Guidance adoption record

Read this maintenance record when asked to adopt or refresh guidance,
including guidance for a new language or technology. It is not required
reading during routine development and is not active agent guidance.

- Source library: `C:\dev\agent-guidance`, upstream
  <https://github.com/tunnelvisionlabs/agent-guidance>.
- Source revision: `0d354b6b503e69a9c58776166e6de19e0281a1bc`
  (`docs(github): define native issue type handling`). The source working
  tree and index were clean when inspected; no uncommitted source content
  was used.
- Adopted on: 2026-11-14.
- Destination baseline: `cc17a50989930e94de1f835815d952fb4497160a`;
  the working tree and index were initially clean. This is the first
  recorded reconciliation, not a refresh of an earlier adoption record.
- Destination instructions: `AGENTS.md`; `README.md` under **Local Codex
  initialization prompt** keeps the entry instruction and delegates the
  commit-draft convention to `AGENTS.md` instead of repeating it.
- Workflow and template: `skills/adopt-guidance/SKILL.md`,
  `docs/adopting.md`, and `templates/adoption-record.md` at the source revision
  above. Active instructions contain the adopted substance and do not depend
  on the source checkout or require reading this record.

## Selected modules

All paths in the source column are relative to the library checkout.
Team modules have shared project scope. Personal source audiences remain
personal even where existing or explicitly chosen project conventions apply.

| Module ID | Source path | Agreed scope and destination | Local adaptations and reasons |
| --- | --- | --- | --- |
| shared.defaults | `guidance/shared/defaults.md` | Shared; `AGENTS.md`, **Project and working conventions** and **Validation commands and tooling** | Python/HA paths and actual tooling replace generic examples; preserve unrelated work and proportionate validation. |
| shared.documentation | `guidance/shared/documentation.md` | Shared; `AGENTS.md`, **Documentation and API contracts**; existing **GitHub issues and stories** | Condense reader/task focus, useful summaries, local qualifications, and proportional detail; preserve existing issue-specific presentation. |
| shared.documentation-lifecycle | `guidance/shared/documentation-lifecycle.md` | Shared; `AGENTS.md`, **Documentation and API contracts** and **GitHub issues and stories** | Code/contracts establish implemented behavior, README and usage guides explain current use, Issues owns future work, and completed work/Git retain history. Keep relevant protocol evidence and unresolved hardware acceptance. Do not infer project readiness from scaffolding language or issue closure. |
| shared.api-contracts | `guidance/shared/api-contracts.md` | Shared; `AGENTS.md`, **Documentation and API contracts** | Use Python docstrings for new/touched reusable contracts. Emphasize units, async ownership/cancellation, and accepted requests versus confirmed valve results. No documentation-tool installation, runtime redesign, or broad reflow. |
| shared.sensitive-data | `guidance/shared/sensitive-data.md` | Shared; `AGENTS.md`, **Sensitive data and live systems** | Apply to valve identities, passcodes, captures, logs, and reference evidence. Verify local ignore coverage before sensitive writes; retain intentional artifacts. Library attribution is confined to this user-requested maintenance record. |
| shared.live-system-access | `guidance/shared/live-system-access.md` | Shared; `AGENTS.md`, **Sensitive data and live systems** | Name live HA installation, valve-setting, firmware, and publication boundaries. Discovery/authentication can have effects. Reuse existing explicit authorization; no extra approval after authorized preparation. |
| strategies.behavior-testing | `guidance/strategies/behavior-testing.md` | Shared; `AGENTS.md`, **Testing** and existing planning/test-sequencing bullets | Retain stronger local production-path and isolation evidence rules. Add independent expectations, bounded waits/resource cleanup, relevant failures, and focused negative guards without duplicating the HA rules. Omit recognition-specific training/evaluation advice because there is no recognition model. |
| languages.home-assistant-testing | `guidance/languages/home-assistant-testing.md` | Shared; existing `AGENTS.md`, **Testing**, retained unchanged before the appended general test-discipline bullets | Existing guidance is the source of this library module. Preserve real HA fixtures, published transitions, faithful Bluetooth fakes, old/replacement session data, timing relationships, and physical-evidence limits verbatim. |
| strategies.incremental-delivery | `guidance/strategies/incremental-delivery.md` | Shared; existing `AGENTS.md`, **GitHub issues and stories**; **Project and working conventions** | Retain reviewable steps, real dependencies, same-step behavior/failure coverage, first-consumer test infrastructure, and loadable steps. Extend first-consumer reasoning to other abstractions/dependencies. CI already defines Ubuntu/Windows targets; impose no new platform order. |
| strategies.github-planning | `guidance/strategies/github-planning.md` | Shared; `AGENTS.md`, **GitHub issues and stories** | Retain HA/valve examples, closed technical details, native relationships, and acceptance boundaries. Add issue discovery, current-body review, native type discovery/API fallback/read-back, explicit observation/design status, body-file writes, and Issues as the future-planning authority. Native-type availability is to be discovered when operating on issues, not assumed from labels or a connector's omitted field. |
| strategies.reproducible-verification | `guidance/strategies/reproducible-verification.md` | Shared; `AGENTS.md`, **Validation commands and tooling** | Use actual Python/pip/pytest commands, separate minimum/current HA environments, existing socket isolation, and pinned dependencies/actions. No configured formatter or linter is invented. Prose changes use document/link/diff review rather than redundant behavior tests. |
| personal.git | `guidance/personal/git.md` | Personal source audience; user explicitly chose shared project scope. `AGENTS.md`, **Git authorization and message construction**, **Commit message guidelines**, and **GitHub issue closure** | Keep separate staging/commit authorization, explicit staging paths, closure/acceptance rules, actual-newline construction, and stored-message verification. Retain local message-content rules, clarify non-obvious validation versus routine success, and move the fenced-draft rule from README to its single active home. The copied author rules affect this repository only; no global settings changed. |
| personal.writing | `guidance/personal/writing.md` | Personal source audience; user explicitly chose shared project scope. `AGENTS.md`, **Writing and review presentation**, plus existing **GitHub issues and stories** | Preserve the existing issue-review draft bullet unchanged and refer to it for other drafts. Add the Oxford comma and PR title/final-diff defaults; reuse documentation and change-reporting rules rather than adding a second complete writing policy. |

### Equivalent existing guidance retained unchanged

These mappings identify retained text, rather than implying that every
source rule had to be copied. Destinations refer to the existing sections;
the source revision and destination baseline above make the original wording
recoverable.

| Source module and rule family | Existing destination retained unchanged | Evidence and interpretation |
| --- | --- | --- |
| languages.home-assistant-testing: production objects, real framework fixtures, published state/transitions, faithful Bluetooth boundary, clocks, distinguishable sessions, and physical-evidence limits | Original `AGENTS.md` **Testing** bullets, from “Prefer regression tests” through “completion of a mocked call or task alone does not establish them” | Added in `9fc5db85809b586d75eb5c7294ba868ebfe5d601` (2026-11-05). The library's `docs/sources/home-assistant-testing.md` explicitly derives the ecosystem module from that change. |
| strategies.behavior-testing: public behavior, production paths, faithful shared fakes, evidence-based isolation, scenario names, and explanations of subtle risks | The same original **Testing** block | The local version adds concrete HA state, ownership, cancellation, and backend timing detail. Keep those stronger refinements instead of appending a generic second testing policy. |
| strategies.incremental-delivery: reviewable steps, real dependencies, same-step tests, first-consumer infrastructure, and loadable steps | Existing **GitHub issues and stories** bullets beginning “A story organizes”, “Plan each story”, “Add or update relevant tests”, and “Introduce test infrastructure” | Established by `c41ed780e1192e039eed089d181ce725ad7a7568` (2026-11-06); retain the HA-specific behavior/failure examples and allowance for several commits per issue. |
| strategies.github-planning / shared.documentation: problem-first issues, deliverable verbs, investigations, observable acceptance, collapsed detail, global-rule placement, and real relationships | Existing **GitHub issues and stories** bullets beginning “Write a readable title”, “Use familiar action verbs”, “For investigations”, “For implementation or acceptance”, “Put issue-specific”, “Keep repository-wide”, and “Use GitHub Relationships” | The library's `docs/sources/github-planning.md` cites `c41ed780`; there is no reason to replace this repository's tailored examples. |
| strategies.github-planning / personal.writing: full review drafts, stable alternatives, and no redundant approval | Existing **GitHub issues and stories** bullet beginning “When showing a draft for review” | Already an explicit shared project practice; preserving it does not opt the project into the rest of personal.writing. |
| personal.git: Conventional Commits, imperative/specific subject, 50/72 limits, contextual body, defaults, tradeoffs, and compatibility | Existing `AGENTS.md` **Commit message guidelines** | Introduced by `d0a6e644e12d3ec537f7faa78bd8c91c571d69bb` (2026-01-01). Existing local rules take precedence over the module's fallback message defaults. |
| strategies.reproducible-verification / languages.home-assistant-testing: reproducible targets and honest evidence | `README.md` **Development → Tests**, `pyproject.toml`, pinned test requirements, `.github/workflows/tests.yml`, and shared test fixtures | Keep existing commands/configuration as the primary setup and execution contract. No changes to tooling, dependency pins, code, or tests are needed for this adoption. |

The January README convention to suggest a fenced commit-message draft after
repository changes is retained in substance, relocated to `AGENTS.md` under
**Git authorization and message construction**, and removed from the README
prompt to avoid duplicate active instructions. The README's instruction to
read AGENTS.md and README.md is unchanged. Drafting remains separate from
permission to stage or commit.

## Selected skills

No skills were installed or copied. `adopt-guidance` was used from the
explicitly supplied library for this task. `docpass` was evaluated separately:
its optional API-documentation review workflow is useful on request, but
the standing API rules suffice here and this task needs no new installed
workflow. No profile, ancestor instruction, global configuration, or skill
installation was changed.

## Relevant omissions and decisions

### Personal scopes

- On 2026-11-14 the user answered **Share across this project** to both the
  personal.git additions and personal.writing preferences after being offered
  personal, shared-project, and omit choices. Reuse that explicit scope
  decision during later refreshes while the rules and scope still apply.
- The author-origin personal rules are now shared project instructions in
  `AGENTS.md`. Existing user-supplied Git Safety and Commit Message
  Construction constraints are preserved, including separate authorization
  for index changes, commit creation, and amendments. Adopting them is not
  authorization to stage, commit, amend, close issues, push, or merge.
- Existing shared commit conventions override imported fallback message
  defaults. Existing issue-draft presentation is retained unchanged; the
  additional writing preferences refine it without a competing draft format.
- No user-only instruction mechanism was needed because the user selected
  shared project scope. No ancestor/profile/global configuration was changed.

### Conflicts and local adaptations

- **Generic test isolation versus real HA behavior:** use the stronger local
  policy from `9fc5db8`, preserved by the ecosystem module. `tests/conftest.py`
  explains the observed Windows `fcntl` import failure in the upstream plugin
  and constructs real HA objects locally. Keep that working arrangement and
  the configured plugin exclusion; do not interpret “real HA fixtures” as a
  requirement to install the upstream fixture package or stub the framework.
- **Commit validation and prose:** the local January guide requests brief
  validation instructions when the method is not obvious, and prefers
  paragraphs while allowing useful implementation bullets. The library's
  personal.git defaults omit routine successful check results, but explicitly
  defer message guidelines to project-specific instructions. Retain the local
  section. Non-obvious reproduction instructions are different from a routine
  success log; active guidance clarifies that actual routine check outcomes
  belong in the accompanying response and material failed/skipped/unavailable
  checks may belong in the message. Its implementation-summary wording does
  not require a top-level bullet list in every message. The original section
  itself remains unchanged.
- **Plans versus durable evidence:** existing protocol/reliability documents
  mix implementation history, acceptance limits, and earlier sequences. Keep
  their useful evidence and current usage explanations. Future requirements
  and progress belong in Issues; this adoption neither recreates their plans
  nor broadly rewrites historical documents. In particular, a historical
  per-step commit instruction in a story record does not override current
  Git authorization.
- **Hardware acceptance versus completed software:** README and display/
  reliability records explicitly leave physical disconnect, firmware timing,
  and proxy-capacity evidence open. Successful simulation or a closed issue
  cannot silently settle those requirements.
- **Platform and tooling defaults:** retain Python 3.14, both pinned HA
  targets, and the Ubuntu/Windows CI matrix. Do not import npm, native-binary,
  C/ESP-IDF, or generic Python formatting conventions from unrelated projects.
- **Artifact privacy:** `git check-ignore -v` confirmed that the existing
  untracked local `local-artifacts/.gitignore` rule (`*`) covers a hypothetical
  file there. That rule is local, not a guaranteed tracked repository policy.
  Active guidance therefore requires checking coverage before sensitive
  writes; no blanket artifact retention/deletion policy or new ignore rule
  was imposed.

### Relevant omissions

- `strategies.windows-test-recovery`: Windows is used, but no confirmed
  Application Control rejection of a locally compiled test executable was
  observed. Python interpreter/import failures do not meet its trigger.
- TypeScript/JavaScript, C/ESP-IDF, Solid/UI styling/feature ownership,
  media-session, embedded-firmware, geometry, printing, and model-release
  modules: their technologies and strategies are absent. Communicating with
  valves/proxies is not development of their firmware.
- Pre-MVP/post-MVP boilerplate and recognition-model evaluation rules: no
  readiness classification or model-training workflow is needed for this
  reconciliation. Keep claims tied to actual current capabilities and limits.
- No automatic source synchronization, routine adoption-record loading,
  broad configuration changes, live device operations, GitHub writes,
  staging, or commits are part of this adoption.

## Verification and limitations

- Inspected the complete tracked file list, root guidance, README, manifests,
  requirements, pytest configuration, CI workflow, production API examples,
  shared Bluetooth/clock/HA fixtures, and relevant protocol/evidence docs.
  There is one integration, no language-specific subprojects, and no existing
  scoped agent instructions or adoption record to reconcile separately.
- Inspected full local history for the guidance's January and November
  introductions, the HA restore-state compatibility change, and the source
  library's rationale and recent history. The exact source revision is
  available locally; there is no missing historical-source limitation.
- Verified that documented command/configuration paths exist and match the
  README and CI. The upstream plugin exclusion and simulated Bluetooth
  boundaries are intentional, not a missing dependency to fix.
- Prose/diff review and `git diff --check` passed. A Python 3.14.2 document
  check verified local paths, pytest isolation, pinned HA requirements,
  heading uniqueness, whitespace, code fences, absence of template markers,
  independence from the library checkout, and isolation of this record from
  routine instructions. It also compared the original commit-message
  section and all original testing bullets against HEAD and confirmed that
  both were retained unchanged.
- Compared `git ls-files --stage` with the initial snapshot: all index
  entries are unchanged. Changes are limited to `AGENTS.md`, `README.md`,
  and this new record, and remain unstaged. No application behavior changed;
  runtime tests and live Bluetooth checks were not run for these prose edits.
- Both personal-scope decisions are resolved and reflected in the active
  guide. There are no outstanding material adoption choices.
