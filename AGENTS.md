# Agent guide

## Project and working conventions

- This is a Python Home Assistant custom integration for Chandler water-system
  valves. Production code is in `custom_components/chandler_legacy_view/`;
  `tests/` contains real Home Assistant fixtures and simulated Bluetooth I/O.
- Read `README.md` for supported behavior, installation, and development setup.
  Inspect the relevant code, tests, manifests, and applicable instructions
  before choosing an approach. Follow existing Python and Home Assistant idioms.
- Keep changes tied to the requested outcome. Preserve unrelated user work;
  avoid speculative abstractions, dependencies, and broad cleanup. Introduce
  infrastructure, dependencies, runtime choices, modules, and abstractions
  with their first real consumer.
- Use existing tools and configuration for mechanical rules. This repository
  has no configured formatter or linter; do not invent required commands or
  introduce tooling merely to impose a style preference. If formatting is
  configured later, format supported files, review the diff, then validate;
  repeat preparation when subsequent edits invalidate it.
- Explain the resulting behavior, material tradeoffs, checks, and remaining
  limits so a reader can assess the work without the originating conversation.

## Validation commands and tooling

- Run commands from the repository root with a Python 3.14 virtual environment.
  `README.md` and the pinned requirements define the current and minimum Home
  Assistant test targets. CI runs both on Ubuntu and Windows; do not infer a
  passing platform or hardware result from another environment's checks.
- For the current target, install with
  `python -m pip install -r requirements-test.txt`; for the minimum, use
  `python -m pip install -r requirements-test-min.txt`. Use separate environments
  for the two targets. Check dependencies with `python -m pip check` and run
  tests with `python -m pytest` (or `python -m pytest -q`, as in CI).
- Preserve the test isolation configured in `pyproject.toml`: the upstream
  Home Assistant pytest plugin is disabled, storage is temporary, and external
  socket I/O is blocked. The local fixtures create real Home Assistant objects;
  introducing framework stubs requires the concrete isolation evidence described
  under Testing. Do not enable host services to run normal tests.
- Choose checks for the change: review prose, links, and formatting for
  documentation-only work; run relevant behavior tests for implementation and
  the supported-target suites for compatibility changes. Keep the standard
  suite part of normal implementation validation. Repeat or broaden checks
  when a new change, failure, or unresolved concern warrants it.
- Retain dependency pins and CI action commit pins. Avoid incidental upgrades,
  moving tags, unpinned downloads, and new tooling without a task-specific need.
  Pin introduced tools and components at their first consumer; preserve the
  existing dependency manager, any lockfiles, and integrity checks.
- Use changed-file commands or explicit paths when unrelated work is present.
  Formatting must preserve the Git index; keep repository-wide style passes
  separately scoped. Respect generated output, private data, caches, unsupported
  formats, and deliberate tool exclusions. Inspect generator destinations,
  validate affected generators, and preserve unrelated outputs and evidence.
- Report actual commands, outcomes, relevant versions/platforms, and gaps.
  Missing tools or blocked checks remain unverified; do not change system
  security policy, install system tools, or operate hardware just to obtain a
  passing local result. Review the final diff and check `git diff --check`.
  Distinguish formatting, generation, compilation, execution, packaging, and
  device acceptance when reporting what the checks establish.

## Git authorization and message construction

The following are the repository author's personal Git rules, adopted for
shared project use. The safety rules are authorization constraints, not style
defaults. Message content follows this repository's guidelines below.

- Treat commit creation and index/staging changes as separate, user-controlled
  actions. Never treat either as an implied finishing step.
- Do not run `git add`, `git commit`, `git commit --amend`, `git reset`,
  `git restore --staged`, or equivalent commands, tools, or plugins that create
  a commit or change the Git index unless the user explicitly and unambiguously
  requests that specific action in the current task.
- Authorization to commit does not authorize staging, and authorization to
  stage does not authorize committing. Requests to finish, wrap up, ship,
  prepare changes, or make work ready do not authorize either action.
- If an operation needs an additional staging or commit action that has not
  been authorized, stop and ask. Preserve existing staging state; leave
  completed changes unstaged by default and report the modified files.
- When staging is authorized, select explicit paths for the requested work,
  inspect the staged diff, and preserve unrelated staged content.
- A request for a commit message authorizes drafting only. After work that
  changes repository files, suggest a message in a fenced code block unless
  the user requests otherwise.

Apply the following construction rules only after commit creation has been
explicitly authorized:

- Prefer a Git action that accepts a complete multiline message as plain text
  and passes it to Git verbatim. If invoking Git from PowerShell, use
  PowerShell syntax without routing the message through another shell.
- Prefer a PowerShell here-string for multiline messages. In a PowerShell
  double-quoted string, a newline is `` `n `` and a blank line is `` `n`n ``;
  use `` `r`n `` only when CRLF is explicitly required.
- Never use literal `\n`, `\\n`, Bash `$'...'` quoting, or another shell's
  escape syntax for PowerShell message line breaks. Hard wrapping must produce
  actual newlines, not visible escape text.
- After creating a commit, verify the stored message with
  `git log -1 --format=%B`. Do not amend without explicit authorization.

The local request for non-obvious validation instructions is retained. Report
routine successful check results in the accompanying response; mention critical
failed, skipped, or unavailable validation in the message when material. Prefer
paragraphs; the implementation-summary guideline does not require a bullet list.

## Commit message guidelines

- Use Conventional Commits style: `feat|fix|chore|docs|refactor|test|build|ci(<scope>): <summary>`.
- Write the subject in imperative mood ("add", "fix", "refactor"), and keep it specific (avoid "update stuff").
- Keep the subject ≤ 50 chars; if a longer explanation is needed, put it in the body.
- Wrap body text at 72 characters.
- At the top level, prefer paragraphs (which can be short) to bulleted lists. Bulleted lists are not prohibited within the prose if they provide valuable organization which is best formatted as a list.
- Separate subject/body with a blank line, and separate paragraphs with a blank line.
- Assume the reader hasn't seen the issue/PR/discussion: include the backstory and *why* the change is needed before the *what*.
- Describe user-visible behavior changes and any important defaults/flags/env vars (especially dev vs packaged behavior).
- List key implementation bullets (high level), not a diff dump; call out notable tradeoffs/constraints.
- Mention how to validate briefly (commands or manual check) when it's not obvious.
- If there's a compatibility risk, note it; use `BREAKING CHANGE:` when applicable.

## GitHub issue closure

- Direct issue closure through an API, connector, CLI, or browser requires
  explicit authorization. Local completion and successful checks do not grant it.
- An authorized resolving commit may use `Fixes #123` for bugs or `Closes #123`
  for completed behavior or tasks without separate issue-closure authorization.
  This does not authorize staging, pushing, or merging.
- Preserve acceptance and merge conditions. Use `Refs #123` for partial work
  or outstanding acceptance, and explain in the accompanying response why each
  referenced issue remains open or what work remains.

## GitHub issues and stories

- Resolve the target repository, search existing issues before creating work,
  and read current bodies and relevant comments before editing.
- Write a readable title and complete body from initial creation. Open with
  the problem, why it matters, and what someone should be able to do or
  understand. Use a concrete Home Assistant or valve example when useful;
  introduce API names, jargon, and internal data structures only when they
  help explain the problem.
- Use familiar action verbs that match the deliverable: Investigate for
  discovery, Add or Implement for behavior, Validate or Verify for
  acceptance, and Decide for choosing among established options.
- Discover whether native issue types are supported and which types are
  available. For new issues, use the appropriate supported type according to
  repository conventions; a similarly named label is not a substitute. If a
  connector cannot read or write the type, use GitHub's API for that operation.
  Set the type during creation or on the created issue while preserving its
  identity, intended content, and metadata. Verify the saved type through an
  interface that exposes it; an omitted field does not verify the type or show
  that types are unsupported. Distinguish unsupported types from tool limitations
  or API/access failures, and report any unresolved
  limitation without claiming the type was verified.
- For investigations, state the questions and expected deliverable,
  including evidence, significant version differences, and remaining
  uncertainties. Identify relevant Home Assistant, Bluetooth backend,
  proxy, or valve firmware differences when they affect the result.
- For implementation or acceptance, lead with observable behavior or
  results and define how completion can be reviewed. Describe relevant
  entity states, availability transitions, settings, or recovery behavior
  rather than prescribing internal structure. Do not impose one rigid
  issue template.
- Distinguish confirmed observations, documented capabilities, assumptions,
  and proposed designs. Update the body when scope changes; comments should
  not become the only current definition of the work.
- Put issue-specific technical requirements below the introduction and
  main outcomes in a closed `<details>` section with a descriptive
  `<summary>`. Leave blank lines around Markdown inside the section.
  Preserve necessary contracts, resource limits, failure cases, and
  acceptance requirements; readability edits must not silently reduce
  the scope.
- Keep repository-wide privacy, testing, authorization, and security rules
  in this guide or the relevant documentation instead of repeating them
  in each issue. Retain a rule in an issue when it defines that issue's
  specific deliverable or behavior. Include a separate collapsed
  agent-guidance section only for instructions unique to the issue.
- Use GitHub Relationships for parents and actual blockers. Do not repeat
  parent links, prerequisite lists, a Leaf designation, or sequence
  metadata in child bodies. Prefer direct blockers over redundant
  transitive dependencies; suggested work order alone is not a blocker.
- A story organizes children that are each a single logical unit of work
  or another story. Keep the overall sequence in the parent story and let
  independent work remain independent.
- Plan each story as a coherent sequence of reviewable issues and commits.
  Each step explains why it is needed, what behavior or capability it
  adds, how it builds on preceding work, and how its result is validated.
  One issue may require several commits; each commit should still make an
  understandable contribution with appropriate validation at that point.
- Add or update relevant tests in the same step that introduces or changes
  the behavior they cover. Each implementation issue owns its behavior
  and failure tests, and its acceptance criteria should identify those
  checks. For example, connection recovery includes ownership and
  cancellation checks, availability changes include published state
  transitions, and settings persistence includes restoration checks.
  A final generic "add tests" issue must not carry coverage that earlier
  implementation steps already needed. Keep completed steps loadable
  and run their applicable checks.
- Introduce test infrastructure when its first consumer needs it. A
  separate issue may deliver a reusable Bluetooth test harness or cover an
  integrated path that first becomes testable there, such as proxy
  recovery or multiple valves sharing connection capacity. Such issues
  extend the evidence from earlier steps. Documentation-only steps can
  use document review and formatting checks without tests for prose.
- For larger work, select the immediate development environment and intended
  Home Assistant deployment environment explicitly. CI's Ubuntu/Windows matrix
  does not select the actual deployment target. Preserve required portability
  and defer unrelated packaging or deployment investigations until their first
  useful validation point; impose no universal platform order.
- Record validation requirements and remaining gaps, including necessary
  Home Assistant versions, adapters, proxies, or valve hardware. Follow
  the testing guidance below when distinguishing simulated I/O from live
  hardware evidence; simulation or successful setup alone does not prove
  physical connection release or recovery.
- When rewriting an issue, preserve relationships, state, labels, native
  issue type, and other metadata. Prefer structured tool arguments;
  for multiline CLI bodies, use a body file with actual newlines. Verify saved
  text, changed relationships, and the native type after writing.
- When showing a draft for review, provide the complete proposed title
  and body, or clearly identify an excerpt. Give alternatives stable
  labels so the user can refer to specific presentations. Do not add a
  review or approval step when the user has already authorized issue
  creation or updates.
- GitHub Issues owns future requirements, priorities, implementation plans,
  and progress. Keep current usage guides and still-relevant protocol evidence
  in the repository; avoid a second full roadmap that needs synchronization.
- Planning steps or local checks do not authorize commits, external writes,
  or issue operations, or establish outstanding hardware acceptance or merge
  conditions. Follow the user's applicable authorization constraints.

## Testing

- Prefer regression tests that exercise production behavior and assert
  observable outcomes over tests that assert implementation details.
- Treat Home Assistant entity states, availability, state-change events,
  API behavior at module boundaries, and persistent side effects such as
  restored settings and registry changes as observable behavior.
- Prefer asserting through a module's public surface or Home Assistant's
  state machine and events instead of private helpers, internal attributes,
  or incidental call patterns, unless those internals are themselves part
  of the contract. When transition order matters, assert the published
  sequence as well as the final state.
- Prefer production objects by default, including the integration's
  connection and discovery managers, entities, and protocol parsers. Use
  focused fakes at true external boundaries, such as Bluetooth adapter and
  GATT I/O, proxy transport, networks, or non-controlled file systems.
- Prefer real Home Assistant test fixtures for the state machine, event
  bus, config entries, entity lifecycle, registries, and state restoration.
  Apply the same evidence requirements for isolation when replacing these
  framework objects with stubs.
- A fake should be a shared test implementation that faithfully
  reproduces the real dependency's observable behavior as closely as
  practical. For Bluetooth dependencies, model the relevant notification,
  connection ownership, cancellation, and disconnect behavior. Prefer
  production behavior plus shared fakes over ad hoc mocks or test-specific
  behavior overrides.
- Use test-specific behavior overrides only when they are necessary to the
  core intent of the test and the same scenario cannot be covered through
  standard observable behavior of the production object or shared fake.
- When shortening delays or using a controlled clock, preserve the
  production relationships between operation deadlines, cleanup budgets,
  retries, and watchdog checks. Use distinguishable data from old and
  replacement connections when testing ownership or recovery.
- Use isolation to address problems that have actually been observed in
  this codebase, such as measured slow tests, demonstrated flaky behavior,
  or setup that depends on uncontrolled external state. Do not isolate
  based only on a hypothesis that broader tests might become slow,
  brittle, or flaky.
- When considering isolation for a predicted risk, state the concrete
  evidence behind the prediction. Prefer direct measurements, repeated
  local observations, CI history, peer-reviewed research, or guidance from
  recognized practitioners with a track record of pragmatic engineering
  advice. Do not treat an uncited convention or a random blog post as
  enough evidence to decouple tests from production behavior.
- Write tests so a future reader can understand the scenario, the expected
  outcome, and why a failure matters to users or consumers of the module.
- Choose test names, fixtures, and assertions that make the behavioral
  intent clear in the context of the integration or module being tested.
- If a test covers a subtle invariant or regression, add a brief comment
  explaining the user-facing or consumer-facing risk that the test is
  protecting against.
- Report whether validation uses simulated Bluetooth I/O or live hardware.
  Claims about physical disconnects or released proxy connection slots
  require evidence from the corresponding hardware or transport boundary;
  completion of a mocked call or task alone does not establish them.
- Derive expected values independently of the implementation under test.
  Include relevant failure and boundary cases; do not weaken assertions or
  security checks to obtain a pass. A focused mock may guard an operation that
  must not occur, without replacing the production behavior under test.
- Prefer controlled clocks, events, and existing fixtures over sleeps. Bound
  waits and release test-owned resources; preserve genuine randomness when
  independent keys or tokens are part of the behavior being tested.
- Assert observable errors and emitted commands as well as state and persistent
  effects. Avoid incidental ordering, arbitrary object counts, and whole-output
  snapshots unless they are explicit contracts. Explain internal assertions
  protecting ownership, resource bounds, or security. Use real temporary files
  for file behavior when practical.
- Keep shared fakes small and focused while preserving relevant failures, event
  ordering, ownership, cancellation, and completion at the external boundary.
- Name scenarios by their condition and expected outcome, and make assertion
  failures useful. Documentation-only and reversible, low-impact edits do not
  need tests that merely restate prose or the implementation.

## Documentation and API contracts

- Write for the reader's task or decision. Establish purpose, useful behavior,
  and scope before mechanics; supply enough context for readers who did not
  follow the discussion. Describe applicability accurately without implying
  unsupported uses or excluding supported ones. Organize the whole piece so
  each section advances the reader's goal; give distinct reference tasks useful
  entry points instead of forcing them into one narrative.
- Keep summaries dependable when read alone. Put material qualifications with
  the claims or operations they change. Use familiar terms, direct labels,
  examples, or tables when they reduce interpretation. Label comparisons,
  units, and relationships directly. Preserve facts and limits when shortening;
  scale detail to the audience, importance, and complexity, and stop when the
  reader's needs are met. Use predictable organization and local context for
  readers entering or skipping sections,
  rather than recurring instructions about what to skip. Review both the whole
  piece and standalone statements for dependable conclusions when skimmed.
- Code and public interfaces establish implemented behavior; `README.md` and
  maintained guides such as `docs/polling.md` explain current use. GitHub Issues
  owns planned scope; completed issues, PRs, commits, and older revisions retain
  historical rationale and evidence. Scope claims to the relevant branch or
  release. Identify what content means independently of its container: an open
  issue may describe a current defect, and a closed PR a rejected proposal.
  Closure alone does not prove implementation or readiness.
- Permit summaries, links, and repeated contracts at points of use when they
  serve independent reader needs. Keep the primary source identifiable and
  weigh reader benefit against upkeep; avoid a second complete progress record.
- For a pre-MVP project, state that it is not ready for its intended use,
  explain the useful outcome, identify major gaps, and link authoritative scope
  in Issues. Keep a concise vision in the overview when needed to understand
  the project today. After MVP, lead adoption with available capabilities and
  use; put future ambitions where they do not dominate current users' path.
  Establish readiness from evidence rather than inferring it from scaffolding
  wording or successful local checks.
- Distinguish required outcomes, preferred representations, tentative or chosen
  approaches, observed behavior, and accepted readiness. Label planned
  capabilities and performance targets. Update current contracts and usage
  guides with behavior changes; preserve useful explanations while updating
  status, versions, and evidence. Keep hardware and firmware evidence gaps
  explicit even after implementation.
  A status edit cannot substitute for implementation or acceptance evidence.
  Let maintained contracts and usage guides become authoritative for current
  callers while completed planning records retain historical requirements and
  evidence without having to track later guide changes.
- Remove superseded instructions from the affected version's active guides.
  Retain migration advice, operative rationale, and evidence for current
  limitations where useful. Do not create duplicate archives or rewrite
  historical records solely to synchronize them with current guides.
- For new or touched reusable Python APIs, shared types, meaningful shared
  members, and requested documentation work, document caller contracts so
  callers need not read the implementation. Use native docstrings at the
  declaration callers use. Inspect analogous members; without a local precedent,
  follow idiomatic Python/HA conventions. Reuse accurate terminology and
  structure for equivalent behavior, including repeated parameter meanings;
  make consequential differences apparent in corresponding descriptions.
  These requirements apply without an automated documentation-coverage check.
- Explain accepted inputs, formats, units, ranges, defaults, actual preconditions,
  and meaningful empty, omitted, null, or sentinel values that types do not
  express. Name parameters, fields, options, and individual callback arguments
  where this prevents inference. Cover success, failure, partial or unchanged
  output, exceptions, rejection, fallback, and best-effort behavior.
- Document meaningful members of public options, results, shared records, and
  enums by how values affect behavior. Include side effects, environmental
  dependence, ownership, lifetime, initialization order, event-loop context,
  cancellation, and blocking where relevant to correct use; distinguish an
  accepted request from confirmed valve output.
- Favor predictable related APIs during authorized implementation work.
  Documentation-only work does not authorize redesign. State actual enforced
  behavior without inventing validation, recovery, error results, or concurrency
  guarantees. Keep implementation rationale beside the implementation; explain evidenced
  workarounds with affected HA/backend/firmware versions, the failure avoided,
  and supporting evidence.
- Preserve accurate wording and make proportional repairs instead of stylistic
  rewrites; repeated review should produce little further change. Use a short
  example when it prevents likely misuse. For scripts, document paths,
  prerequisites, exit/exception behavior, and file/subprocess/network effects
  where needed. Comments do not enforce contracts or replace behavior tests.
  Do not introduce a documentation tool or another language's tags for these
  rules.

## Writing and review presentation

These personal presentation preferences are adopted for shared project use,
alongside the documentation practices above.

- Use clear, familiar language, concrete examples, and the Oxford comma in
  prose lists. Explain material tradeoffs, validation, and remaining limits.
- Use concise, action-oriented PR titles naming the concrete change and scope.
  PR titles do not inherit a commit subject's prefix or 50-character limit.
- Align PR titles and descriptions with the final diff. Omit abandoned work
  unless it explains a decision the reviewer needs to assess.
- For proposed PRs and other review drafts, use the same complete-title/body
  and stable-alternative-label practice specified for issue drafts above.
  Follow the destination agent's required draft presentation format.

## Sensitive data and live systems

- Keep real credentials, tokens, QR payloads, private installation addresses and
  device identities, raw captures, databases, and sensitive logs out of tracked
  files, diagnostics, issues, PRs, and shared packages. Use fictional test
  fixtures and examples; ordinary tests must not require private configuration,
  household inventory, a VPN, or real credentials. Client-visible environment
  variables are not private storage. Preserve useful
  requested measurements and configuration records without treating them as
  permission to expose identifying details.
- Before writing sensitive evidence, verify that its repository-local private
  storage is ignored. `local-artifacts/` currently has a local ignore rule;
  verify it in the checkout instead of assuming the directory name is sufficient.
  Preserve intentional evidence and deliverables rather than blanket-deleting
  or ignoring every output directory. Redact identifying details before sharing.
- Keep unrelated private reference names, links, and machine paths out of
  ordinary deliverables. Record source identities only when attribution has
  been authorized in an appropriate maintenance record. Inspect the actual
  sharing package for secrets, local paths, stale artifacts, and raw evidence.
- Local tests, simulation, and package preparation do not authorize installing
  into a live Home Assistant instance, changing valve settings or firmware,
  provisioning, signing, deployment, uploads, publication, or account changes.
  Establish the authorized operation, target, and scope before an external
  write; reuse authorization that still applies without a redundant approval
  step.
- Keep live investigations read-only unless the relevant change is authorized.
  Judge operations by their effects: Bluetooth discovery, authentication, or a
  nominal read can affect a real device. Do not probe mutation endpoints to
  learn their behavior. Use simulated peers for mutation and failure tests.
- Preserve authentication, certificate checks where applicable, and protocol
  integrity. Verify intended device/service identity before credentials or
  writes; an address alone may not establish identity. Reconcile an uncertain
  remote write before retrying; do not silently switch targets, transports, or
  recovery methods after failure. Report the exact evidence obtained rather
  than treating an accepted request as durable remote or physical success.
