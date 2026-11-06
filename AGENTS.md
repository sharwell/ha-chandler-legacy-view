# Agent guide

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

## GitHub issues and stories

- Write a readable title and complete body from initial creation. Open with
  the problem, why it matters, and what someone should be able to do or
  understand. Use a concrete Home Assistant or valve example when useful;
  introduce API names, jargon, and internal data structures only when they
  help explain the problem.
- Use familiar action verbs that match the deliverable: Investigate for
  discovery, Add or Implement for behavior, Validate or Verify for
  acceptance, and Decide for choosing among established options.
- For investigations, state the questions and expected deliverable,
  including evidence, significant version differences, and remaining
  uncertainties. Identify relevant Home Assistant, Bluetooth backend,
  proxy, or valve firmware differences when they affect the result.
- For implementation or acceptance, lead with observable behavior or
  results and define how completion can be reviewed. Describe relevant
  entity states, availability transitions, settings, or recovery behavior
  rather than prescribing internal structure. Do not impose one rigid
  issue template.
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
- Record validation requirements and remaining gaps, including necessary
  Home Assistant versions, adapters, proxies, or valve hardware. Follow
  the testing guidance below when distinguishing simulated I/O from live
  hardware evidence; simulation or successful setup alone does not prove
  physical connection release or recovery.
- When rewriting an issue, preserve relationships, state, labels, and
  other metadata. Verify saved text and changed relationships.
- When showing a draft for review, provide the complete proposed title
  and body, or clearly identify an excerpt. Give alternatives stable
  labels so the user can refer to specific presentations. Do not add a
  review or approval step when the user has already authorized issue
  creation or updates.

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
