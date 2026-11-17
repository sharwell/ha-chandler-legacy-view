# Guidance adoption record

Read this maintenance record when asked to adopt or refresh guidance,
including guidance for a new language or technology. It is not required
reading during routine development and is not active agent guidance.

- Source library: `C:\dev\agent-guidance`, upstream
  <https://github.com/tunnelvisionlabs/agent-guidance>.
- Source revision: `7e057ff583ff07e7117ea3b81e8591dc65ef5175`
  (`docs(adoption): revisit earlier exclusions`). The source working
  tree and index were clean when inspected; no uncommitted source content
  was used.
- Initially adopted on: 2026-11-14, from source revision
  `0d354b6b503e69a9c58776166e6de19e0281a1bc`, recorded in destination commit
  `a2412c1dacab513fdd34460e7b79fb58cf9abe0f`.
- Refreshed on: 2026-11-17.
- Refresh destination baseline: `10308005ee1d51efd36b5d94c3903b2f59329a8f`;
  the working tree and index were initially clean. The original pre-adoption
  baseline was `cc17a50989930e94de1f835815d952fb4497160a`.
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
| shared.documentation | `guidance/shared/documentation.md` | Shared; `AGENTS.md`, **Documentation and API contracts**; existing **GitHub issues and stories** | Preserve task-led structure, accurate applicability, dependable summaries, qualifications at the claim, independent entry/skip context, proportional detail, explicit comparisons/units, and whole-piece plus standalone review. Restore distinctions the prior condensation left implicit; preserve existing issue presentation. |
| shared.documentation-lifecycle | `guidance/shared/documentation-lifecycle.md` | Shared; `AGENTS.md`, **Documentation and API contracts** and **GitHub issues and stories** | Keep code/contracts, usage guides, Issues, and history in their distinct roles. Restore conditional pre-/post-MVP duties, useful repeated contracts, required outcomes versus preferences, labeled targets, and the contract/guide handoff. No new readiness classification is inferred. |
| shared.api-contracts | `guidance/shared/api-contracts.md` | Shared; `AGENTS.md`, **Documentation and API contracts** | Require Python docstrings for new/touched reusable APIs, shared types, meaningful shared members, and requested documentation work. Restore input formats/preconditions/absence, named callback arguments, failure/fallback/unchanged output, public result/enum meanings, environment/lifetime, examples, script effects, and workaround failure details. No coverage checker, tool installation, API redesign, or broad reflow is imposed. |
| shared.sensitive-data | `guidance/shared/sensitive-data.md` | Shared; `AGENTS.md`, **Sensitive data and live systems** | Apply to valve identities, passcodes, QR payloads, captures, logs, measurements/configuration, and reference evidence. Restore the client-visible environment warning and purpose-based evidence retention. Verify local private ignore coverage before writes. Library attribution remains confined to this requested record. |
| shared.live-system-access | `guidance/shared/live-system-access.md` | Shared; `AGENTS.md`, **Sensitive data and live systems** | Retain live HA/valve/firmware/publication boundaries and clarify provisioning/signing. Discovery/authentication can have effects. Reuse explicit authorization, verify identity and protocol integrity, reconcile uncertain writes, and distinguish requests from actual remote or physical results. |
| strategies.behavior-testing | `guidance/strategies/behavior-testing.md` | Shared; `AGENTS.md`, **Testing** and existing planning/test-sequencing bullets | Preserve stronger HA production-path/isolation evidence rules. Restore explicit errors/emitted commands, limits on incidental ordering/counts/snapshots, justified internal assertions, small faithful fakes, real temporary files, useful condition/outcome failures, and low-impact-test exceptions. Recognition training/evaluation remains excluded because there is no trained recognition model. |
| languages.home-assistant-testing | `guidance/languages/home-assistant-testing.md` | Shared; existing `AGENTS.md`, **Testing**, retained unchanged before the appended general test-discipline bullets | Existing guidance is the source of this library module. Preserve real HA fixtures, published transitions, faithful Bluetooth fakes, old/replacement session data, timing relationships, and physical-evidence limits verbatim. |
| strategies.incremental-delivery | `guidance/strategies/incremental-delivery.md` | Shared; existing `AGENTS.md`, **GitHub issues and stories**; **Project and working conventions** | Retain reviewable steps, real dependencies, same-step tests, first-consumer infrastructure, and loadable steps. Restore runtime/module choices at first use and explicit immediate-development/intended-deployment selection, portability, and deferred unrelated investigations. CI does not select the actual deployment environment or a universal platform order. |
| strategies.github-planning | `guidance/strategies/github-planning.md` | Shared; `AGENTS.md`, **GitHub issues and stories** | Retain HA examples, details sections, relationships, same-step tests, full draft review, and acceptance limits. Clarify native-type API fallback for either read or write limitations and creation-time/post-creation assignment preserving identity/content/metadata. Preserve discovery, body-file writes, read-back, and Issues as future-planning authority. |
| strategies.reproducible-verification | `guidance/strategies/reproducible-verification.md` | Shared; `AGENTS.md`, **Project and working conventions** and **Validation commands and tooling** | Retain actual Python/pip/pytest commands, separate HA environments, and socket isolation. Restore first-consumer pins, lockfile/integrity preservation, changed-file formatting/index preservation, generated/cache/private/unsupported exclusions, generator checks, and distinct verification stages. Formatting duties remain conditional on a configured tool; no formatter/linter is invented. |
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

| Skill | Source path and revision | Destination | Local adaptations |
| --- | --- | --- | --- |
| adopt-guidance | `skills/adopt-guidance/SKILL.md` at `7e057ff583ff07e7117ea3b81e8591dc65ef5175` | Invoked from the supplied library for this refresh; not installed | Apply the new preservation checks to prior omissions, including unchanged modules. The source checkout remains an explicit maintenance input only. |
| docpass (evaluated, not selected for installation) | Complete `skills/docpass/` folder at `7e057ff583ff07e7117ea3b81e8591dc65ef5175`; byte-identical to the prior source | None | Its trigger is a requested API-documentation review. This is a guidance-maintenance task, and repository history/configuration establishes no recurring installed DocPass workflow to preserve. Optional skill selection is separate from adopting standing API requirements; omitting installation does not waive those requirements. |

The skill catalog contains no new skill. No skill folders, profiles,
ancestor instructions, or global settings were changed. If a later request
selects a skill for installation, use a supported repository-only destination
and copy its complete folder; broader installation still needs authorization.

## Relevant omissions and decisions

### Three-way refresh and procedure changes

- Both exact source revisions are available locally. Compared the old source
  `0d354b6b503e69a9c58776166e6de19e0281a1bc`, the current source
  `7e057ff583ff07e7117ea3b81e8591dc65ef5175`, and destination instructions
  at `10308005ee1d51efd36b5d94c3903b2f59329a8f`.
- All 13 previously selected modules and the complete `docpass` folder are
  unchanged between those sources. The adoption procedure, adoption skill,
  record template, catalog, and new C#/.NET modules changed. Therefore a
  changed-modules-only refresh would miss the material corrections below.
- Source commits `f12899d` and `7e057ff` require independent consideration of
  applicability, intended policy, enforcement, and current practice; preservation
  of decision-relevant conditions/exceptions; and re-auditing prior omissions
  even in unchanged modules. The revised skill and template now require that
  audit and its restoration evidence. Applied these requirements to every
  selected module and to the prior exclusions, rather than treating the old
  record's assumptions as approved policy.
- Destination `AGENTS.md` and this record had no intervening changes after
  the initial adoption commit `a2412c1`. Intervening development added raw
  advertisement callbacks and `tests/bluetooth_replay.py`, which exercises
  production HA scanner merging/callbacks. The repository remains Python/HA
  with the same test targets and no new language subprojects or formatting/
  documentation-coverage tooling. Current manifests and source history support
  retaining that setup; they do not establish that uncovered guidance is optional.

### Material requirements restored or corrected

| Module/rule family | Earlier omission or simplification | Final mapping and reason |
| --- | --- | --- |
| shared.documentation: applicability, whole-piece structure, independent entry/skip context, scale, labels, and review | The earlier summary mentioned reader focus but lost explicit decision tests for structure, unsupported/narrow claims, skipped elaboration, and sufficient detail. | `AGENTS.md`, first two **Documentation and API contracts** bullets: restore those authoring duties. No local instruction conflicts with them; brevity alone was not justification for losing their meaning. |
| shared.documentation-lifecycle: conditional pre-/post-MVP guidance | The old record called these “boilerplate” because this maintenance task did not need a readiness classification. That agent inference did not establish a repository exemption. | **Documentation and API contracts**, conditional readiness bullet: restore intended-use readiness, useful outcome, major gaps/authoritative scope, concise vision before MVP, and current capabilities/use after MVP. Keep the conditions without inventing a current MVP status or rewriting README during adoption. |
| shared.documentation-lifecycle: authority, useful repetition, requirements versus preferences, labeled targets, status/acceptance, and handoff | Some distinctions were collapsed into “plans/chosen/observed/ready”; omission could make useful repeated contracts look forbidden or status edits look sufficient. | The lifecycle bullets in **Documentation and API contracts** now preserve these distinctions, including rejected proposals and the current-guide/historical-planning handoff. Existing GitHub planning authority remains; detailed historical evidence is not discarded. |
| shared.api-contracts: coverage and caller-visible details | “Reusable APIs and shared types” plus a short list did not explicitly preserve meaningful shared members, requested review work, named callback arguments, empty/omitted/null values, failure/fallback/unchanged output, enum/result meanings, environment dependence, misuse examples, script effects, or workaround failures. | The API bullets now retain each applicable duty, including comparisons with local precedents and minimal/stable repairs. `pyproject.toml`/CI have no documentation-coverage check; that is an enforcement fact, not a coverage exemption. The requirement can be relaxed by an explicit future user choice, but no such choice is needed or inferred here. |
| strategies.behavior-testing: assertion limits and external boundaries | Generic “observable behavior” left limits on incidental ordering/counts/snapshots, justified internal assertions, small fakes, real temporary files, and useful condition/outcome diagnostics incomplete. | Appended **Testing** bullets restore those distinctions and explicit errors/emitted commands. Keep the existing production-path, clock, isolation, and hardware-evidence rules; preserve the documentation-only/reversible-low-impact exception. |
| languages.home-assistant-testing / strategies.behavior-testing: evidence-based framework isolation | The initial **Validation commands and tooling** text forbade framework stubs absolutely, despite the original **Testing** policy allowing evidence-supported isolation. | Replace that unsupported blanket prohibition with the existing concrete-evidence requirement. Keep real HA objects by default, upstream-plugin exclusion, temporary storage, and host-I/O blocking. `9fc5db8`, current **Testing**, and `tests/conftest.py` support this policy; no fixture implementation changed. |
| strategies.reproducible-verification: first-consumer pins, lockfiles/integrity, formatting scope/exclusions, generators, and evidence stages | Preserving existing pins and having no formatter did not preserve future introduced-tool pins or conditional formatting/check preparation duties. | **Project and working conventions** and **Validation commands and tooling** now retain those conditions, changed-file/index safety, generated/private/cache/unsupported exclusions, and generator validation. No formatter or package manager is installed or changed; absent tooling is not conflicting policy. |
| strategies.incremental-delivery: first-use choices and development/deployment selection | The old record substituted the Ubuntu/Windows CI matrix for explicit platform selection and omitted runtime/module choices from first-consumer reasoning. | **Project and working conventions** and **GitHub issues and stories** restore these duties, required portability, and deferral until useful validation. A test matrix is not the actual deployment environment; no fixed platform order is imposed. |
| strategies.github-planning: native issue-type read/write fallback and assignment | The condensed fallback referred only to a connector omitting a field, leaving a connector unable to write the field and the timing/identity constraints unclear. | **GitHub issues and stories** now covers either read or write limitation, creation-time or post-creation assignment preserving the issue, and independent verification. No issue operations were performed. |
| shared.sensitive-data / shared.live-system-access: specific privacy and authorization cases | QR payloads, client-visible environment variables, requested measurement/configuration evidence, provisioning, and signing were left implicit. | **Sensitive data and live systems** now names these cases without weakening authorization. Apply private-storage/ignore verification and purpose-based retention; no sensitive evidence, system settings, or live devices were changed. |

The other source requirements are preserved by the selected-module mappings
and equivalent existing guidance above. Condensation is retained only where
the resulting rule preserves its applicable decision, condition, and exception.
No documentation requirement is treated as optional merely because a checker
is absent, and no old record entry is treated as evidence of user consent.

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
- The two personal modules are unchanged and there are no newly applicable
  personal modules. Reused the explicit 2026-11-14 choices without asking again.

### Conflicts and local adaptations

- **Test fixture policy and current enforcement:** the generic and HA modules
  agree with the evidence-based local policy from `9fc5db8`. `tests/conftest.py`
  explains the observed Windows `fcntl` import failure in the upstream plugin
  and constructs real HA objects locally. Keep that working arrangement and
  the configured plugin exclusion; do not interpret “real HA fixtures” as a
  requirement to install the upstream fixture package or stub the framework.
  Preserve the policy's evidence-supported isolation exception as corrected
  above; the earlier blanket prohibition was an adoption artifact, not an
  explicit user choice.
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
  targets, and the Ubuntu/Windows CI matrix, verified in README, requirements,
  and `.github/workflows/tests.yml`. That matrix is not a substitute for the
  restored development/deployment selection duty. Do not import npm, native-binary,
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
  observed. The current workflow runs Python/pytest and builds no repository
  test executable. Python interpreter/import failures are explicitly outside
  the module's trigger. This is an applicability exclusion, not inferred policy
  against recovery; reconsider if that specific trigger appears.
- Newly cataloged `languages.csharp` and `languages.dotnet-xml-docs`: no C#
  files, .NET project/solution files, or .NET toolchain appears in the tracked
  tree or CI. Their applicability conditions do not match this Python integration.
  Absence of documentation tooling was not used to reject an otherwise matching
  documentation rule.
- TypeScript/JavaScript, C/ESP-IDF, Solid/UI styling/feature ownership,
  media-session, embedded-firmware, geometry, printing, and model-release
  modules: their technologies and strategies are absent. Communicating with
  valves/proxies is not development of their firmware. Rechecked the full
  tracked inventory, manifests, and CI; no new matching subproject appeared.
- Recognition-specific training/tuning/held-out evaluation within
  strategies.behavior-testing: no trained recognition model or evaluation
  pipeline exists; `models.py` represents valve data. Retain the independently
  derived expectation rule that applies to these protocol tests. Conditional
  pre-/post-MVP guidance is restored and is no longer an exclusion.
- Source-language examples (TypeScript tags, C/CMake or native resource
  formatter assumptions, npm/pnpm/SDK setup) remain excluded as foreign
  mechanics, while their applicable general contracts/checking duties are
  preserved using Python docstrings and this repository's verified commands.
- Slicing/printing/manufactured artifacts are absent, so their live-action
  examples and retention mechanics are not copied. General external-action
  authorization and purpose-based evidence retention remain in force.
- No automatic source synchronization, routine adoption-record loading,
  broad configuration changes, live device operations, GitHub writes,
  staging, or commits are part of this adoption.

## Verification and limitations

- Inspected the complete tracked file list, root guidance, README, manifests,
  requirements, pytest configuration, CI workflow, production API examples,
  and shared Bluetooth/clock/HA fixtures including the newly added replay
  boundary. There is one integration and no additional language-specific
  subproject or scoped agent instruction file.
- Compared the exact old/current library revisions, changed procedure/skill/
  template/catalog, all 13 selected modules including unchanged source text,
  complete skill folders, prior exclusions, this record, and current repository
  instructions. Checked the original guidance/adoption history and current
  development changes. Both source revisions are available; there is no
  missing-history limitation.
- Verified that documented command/configuration paths exist and match the
  README and CI. The upstream plugin exclusion and simulated Bluetooth
  boundaries are intentional, not a missing dependency to fix.
- Prose/diff review and `git diff --check` passed. A Python 3.14.2 document
  check verified local paths, pytest isolation, pinned HA requirements,
  heading uniqueness, whitespace, code fences, absence of template markers,
  independence from the library checkout, and isolation of this record from
  routine instructions. It also compared the commit-message guidelines,
  authorization/closure rules, writing preferences, issue-review draft rule,
  and existing testing block against the refresh baseline and confirmed
  that these retained practices are unchanged. The source-module IDs and
  paths match the catalog metadata; the source revision remains clean.
- Compared `git ls-files --stage` with the initial snapshot: all index
  entries are unchanged. Refresh edits are limited to `AGENTS.md` and this
  existing record, and remain unstaged. README, code, tests, manifests, CI,
  and tool configuration were not changed. No application behavior changed;
  runtime tests and live Bluetooth checks were not run for these prose edits.
- API documentation coverage is required by the adopted guidance but has no
  automatic coverage check. Existing diagnostic/tool configuration is retained;
  absent enforcement is not a policy exception. No new tool was installed.
- Both personal-scope decisions are resolved and reflected in the active
  guide. There are no outstanding material adoption choices.
