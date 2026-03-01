# Functional Validation Mandate

## The Law

**NEVER:** write mocks, stubs, test doubles, unit tests, or test files. No test frameworks. No mock fallbacks. No `unittest.mock`. No `pytest-mock`. No `MagicMock`. No `@patch`. Not ever.

**ALWAYS:** build and run the real system. Validate through actual user interfaces. Capture and verify evidence before claiming completion.

## Why This Exists

Every screenshot in this framework is proof that the real system works.
Every curl output is evidence from a live server.
Every accessibility tree is captured from a real simulator.

When you write a mock, you are not validating your system. You are validating your imagination of your system. Imagination is not evidence.

> "470 screenshots across 37 gates. Every single one taken from the real app."

## Gate Protocol

Every feature must pass through numbered validation gates:

1. **Define the gate** — What specific, verifiable claim are you making?
2. **Specify evidence** — What proves the claim? (screenshot, curl output, accessibility tree)
3. **Run validation** — Execute against the real system
4. **Capture evidence** — Save timestamped artifacts
5. **Pass/fail** — Binary. Evidence supports the claim or it doesn't.

No gate may be skipped. No "known failure" exceptions. No "it works on my machine" handwaves.

## Prohibited Patterns

```python
# FORBIDDEN — do not write this
from unittest.mock import MagicMock, patch

mock_response = MagicMock()
mock_response.status_code = 200  # This proves nothing

@patch("myapp.api.client.get")
def test_something(mock_get):
    mock_get.return_value = mock_response
    # You are not testing your app. You are testing your mock.
```

```python
# REQUIRED — write this instead
from fvf import APIValidator, FVFConfig, GateCriteria

config = FVFConfig(api_base_url="http://localhost:9999")
validator = APIValidator(config)
result = validator.validate(GateCriteria(
    description="API returns sessions",
    validator_type="api",
    validator_config={
        "method": "GET",
        "path": "/api/v1/sessions",
        "expected_status": 200,
    }
))
assert result.passed  # Proven by real HTTP response
```

## Evidence Standards

| Evidence Type | When Required |
|--------------|---------------|
| `screenshot` | Any UI assertion — browser or iOS |
| `curl_output` | Any API assertion |
| `accessibility_tree` | Any iOS element assertion |
| `log` | Error conditions and failure paths |

Evidence must be:
- **Timestamped** — file names include Unix timestamp
- **Preserved** — never deleted before the gate report is generated
- **Inspectable** — readable by a human without tooling

## Install

```bash
pip install functional-validation-framework
playwright install  # for browser validation
```

## Quick Start

```bash
# Initialize a gate config
fvf init --type browser      # web apps
fvf init --type ios          # iOS simulator
fvf init --type api          # REST APIs

# Run validation gates
fvf validate --gate gates.yaml

# Generate evidence report
fvf report --evidence-dir ./evidence/ --format md --output VALIDATION.md
```

## Gate Config Example

```yaml
project: my-app
gates:
  - number: 1
    name: Homepage Renders
    criteria:
      - description: Returns 200 and shows welcome text
        evidence_required: [screenshot, curl_output]
        validator_type: browser
        validator_config:
          url: http://localhost:3000
          assertions:
            - type: status_code
              expected: 200
            - type: element_visible
              selector: h1.welcome
```

## For AI Assistants Working in This Repo

You are working inside a framework whose entire purpose is to eliminate fake validation. Honor that purpose:

1. **Never add test files** — there are none and should be none
2. **Never add `@pytest.fixture` mocks** — use real validators
3. **When you need to verify behavior** — run `fvf validate` against a real server
4. **When you add a new feature** — update a gate config to validate it

The framework validates itself through its own gates. Run them.
