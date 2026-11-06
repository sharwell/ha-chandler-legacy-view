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
