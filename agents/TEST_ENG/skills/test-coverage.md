# Test Coverage Guidance

## Workflow

1. **Read the architecture spec** from `agents/SYS_ENG/output/architecture/<feature-name>.md`
2. **Read the implementation** files listed in the spec
3. **Read existing tests** to understand the project's test framework and conventions
4. **Write the test plan** to `agents/TEST_ENG/output/test-plans/<feature-name>.md`
5. **Write executable tests** in the project's conventional test directory
6. **Verify** each test by reading it back and reasoning about its validity

## Test Plan Requirement

You must write the test plan markdown before writing executable tests. The plan is the contract that justifies each test's existence.

## Framework Discovery

Before writing tests, identify the test framework:

- Python: pytest, unittest
- JavaScript/TypeScript: jest, vitest, mocha
- Other: match the framework already in use

Read existing test files to copy:

- Import style
- Fixture/setup patterns
- Assertion style
- Mocking conventions
- File naming

## Coverage Checklist

For every feature, ensure you have considered:

- [ ] Happy path — normal valid input
- [ ] Sad path — invalid, malformed, or unauthorized input
- [ ] Boundary — empty, zero, maximum, minimum, null, first/last
- [ ] Integration — interaction with other components
- [ ] State/mutation — sequential calls and side effects
- [ ] Error handling — exceptions, status codes, messages
- [ ] Security — auth, injection, leakage (if applicable)
- [ ] Performance — specified latency/throughput (if applicable)

## Test Quality Rules

- One behavior per test
- Descriptive test names
- Clear arrange/act/assert structure
- No shared mutable state between tests
- Mock external boundaries, not the unit under test
- At least one assertion per test
- Every test has a rationale in the test plan

## Tool Usage

- `read_directory_tree(path, max_depth)` — explore repo structure
- `read_file(path, max_lines)` — read specs, source, and existing tests
- `write_file(path, content)` — write test plans and test code
