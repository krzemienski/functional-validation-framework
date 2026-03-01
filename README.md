# functional-validation-framework

[![PyPI version](https://img.shields.io/pypi/v/functional-validation-framework.svg)](https://pypi.org/project/functional-validation-framework/)
[![Python versions](https://img.shields.io/pypi/pyversions/functional-validation-framework.svg)](https://pypi.org/project/functional-validation-framework/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Real systems. Real evidence. No mocks.**

A pip-installable Python framework for functional validation in AI-assisted development. Every assertion runs against your actual running application and saves timestamped artifacts as proof.

> "470 screenshots across 37 gates. Every single one taken from the real app."

---

## The Problem with Mocks

When AI agents write test suites, they almost always write mocks. And mocks don't validate systems — they validate the AI's assumptions about the system. You end up with 100% passing tests and a broken app.

Functional Validation Framework forces a different workflow:

1. **Start the real system** (browser, iOS simulator, API server)
2. **Run gates** against the live system
3. **Collect evidence** (screenshots, curl output, accessibility trees)
4. **Gate progression** on real evidence — no evidence, no pass

---

## Architecture

```
fvf/
├── validators/
│   ├── browser.py      ← Playwright headless Chromium
│   ├── ios.py          ← idb + xcrun simctl
│   ├── api.py          ← httpx HTTP client
│   └── screenshot.py   ← Pillow pixel comparison
├── gates/
│   ├── gate.py         ← Numbered gate runner with dependency ordering
│   ├── evidence.py     ← Timestamped artifact persistence
│   └── report.py       ← MD / JSON / HTML report generation
├── config.py           ← fvf.yaml / pyproject.toml [tool.fvf]
└── cli.py              ← Click CLI: validate, gate, report, init, evidence
```

---

## Install

```bash
pip install functional-validation-framework

# Install Playwright browsers (required for browser validation)
playwright install
```

---

## Quick Start

```bash
# Scaffold a gate config
fvf init --type browser   # web apps
fvf init --type ios       # iOS simulator
fvf init --type api       # REST APIs

# Edit the generated YAML to match your app, then run:
fvf validate --gate browser-gates.yaml

# Generate a report from collected evidence
fvf report --evidence-dir ./evidence/ --format md --output VALIDATION.md
```

---

## Gate Configuration

Gates are defined in YAML. Each gate has a number (execution order), optional `depends_on`, and one or more `criteria`.

```yaml
project: my-app

gates:
  - number: 1
    name: API Health Check
    description: Verify the server is running and healthy
    criteria:
      - description: /health returns 200 within 500ms
        evidence_required: [curl_output]
        validator_type: api
        validator_config:
          method: GET
          path: /health
          base_url: http://localhost:9999
          expected_status: 200
          max_response_time_ms: 500

  - number: 2
    name: Homepage Renders
    description: Verify the UI loads correctly
    depends_on: [1]
    criteria:
      - description: Homepage shows welcome heading
        evidence_required: [screenshot, curl_output]
        validator_type: browser
        validator_config:
          url: http://localhost:3000
          assertions:
            - type: status_code
              expected: 200
            - type: element_visible
              selector: h1.welcome
            - type: text_content
              selector: h1.welcome
              expected: Welcome

  - number: 3
    name: iOS Home Screen
    description: Verify the iOS app launches and shows the home screen
    depends_on: [1]
    criteria:
      - description: Home screen elements visible
        evidence_required: [screenshot, accessibility_tree]
        validator_type: ios
        validator_config:
          deep_link: myapp://home
          assertions:
            - type: element_present
              label: Home
```

---

## CLI Reference

```
fvf validate --gate <gates.yaml> [--config fvf.yaml]
    Run all gates. Exits 0 if all pass, 1 if any fail.

fvf gate run <number> --gate-file <gates.yaml>
    Run a single gate by number.

fvf gate list <gates.yaml> [--evidence-dir ./evidence/]
    List gates with evidence counts.

fvf report --evidence-dir ./evidence/ [--format md|json|html] [--output report.md]
    Generate a validation report.

fvf init [--type browser|ios|api]
    Scaffold a new gate config file.

fvf evidence list [--gate N]
    List collected evidence files.

fvf evidence clean [--gate N] [--keep 3]
    Remove old evidence attempts.
```

---

## Configuration

FVF auto-discovers `fvf.yaml` or `pyproject.toml [tool.fvf]` by walking up from CWD.

```yaml
# fvf.yaml
evidence_dir: ./evidence
screenshot_format: png
browser_timeout: 30000        # ms
ios_simulator_udid: "50523130-57AA-48B0-ABD0-4D59CE455F14"
api_base_url: http://localhost:9999
gate_retry_limit: 3
parallel_gates: false
```

Or in `pyproject.toml`:

```toml
[tool.fvf]
evidence_dir = "./evidence"
ios_simulator_udid = "50523130-57AA-48B0-ABD0-4D59CE455F14"
api_base_url = "http://localhost:9999"
```

---

## Validators

### Browser (Playwright)

Navigates to URLs with a real headless Chromium browser. Supports:
- Status code assertions
- Element visibility checks
- Text content assertions
- Click, fill, wait, navigate actions

```python
from fvf import BrowserValidator, FVFConfig, GateCriteria, EvidenceType

config = FVFConfig()
validator = BrowserValidator(config)
result = validator.validate(GateCriteria(
    description="Homepage loads",
    evidence_required=[EvidenceType.SCREENSHOT],
    validator_type="browser",
    validator_config={
        "url": "http://localhost:3000",
        "assertions": [{"type": "status_code", "expected": 200}],
    }
))
print(result.status)  # ValidationStatus.PASSED
```

### iOS (idb + simctl)

Interacts with a real iOS Simulator via `idb` and `xcrun simctl`. Supports:
- Deep link navigation
- Tap and swipe actions
- Accessibility tree element assertions
- Screenshot capture

**Requirements:** Xcode, idb (`pip install fb-idb`)

### API (httpx)

Makes real HTTP requests to your running server. Supports:
- Status code assertions
- Response time budget checks
- JSON path value assertions
- JSON key existence checks
- Basic schema validation

### Screenshot (Pillow)

Captures screenshots and optionally compares them to a reference image pixel-by-pixel:

```yaml
validator_type: screenshot
validator_config:
  source: browser           # or ios
  url: http://localhost:3000
  reference_path: ./references/homepage.png
  threshold: 0.95           # 95% similarity required
```

---

## Tool Comparison

| Approach | Real Browser | Real Server | Evidence | CI-Friendly |
|----------|-------------|-------------|----------|-------------|
| **FVF** (this) | ✅ Playwright | ✅ httpx | ✅ Screenshots + curl | ✅ |
| Playwright alone | ✅ | ✅ | Partial | ✅ |
| Agent Browser MCP | ✅ | ✅ | Screenshots | Partial |
| Puppeteer MCP | ✅ | ✅ | Screenshots | Partial |
| pytest + mocks | ❌ | ❌ | ❌ | ✅ |
| Manual QA | ✅ | ✅ | Manual | ❌ |

FVF's differentiator is the **gate system**: numbered, dependency-ordered validation steps that collect and persist evidence. You know exactly what was tested, when, and what it looked like.

---

## Evidence Structure

```
evidence/
  gate-1/
    20240101-120000/
      manifest.json         ← metadata for all artifacts in this attempt
      api-curl-1234.txt     ← curl command + response body
  gate-2/
    20240101-120010/
      manifest.json
      browser-screenshot.png
      ios-a11y-tree.json
```

Each gate attempt gets its own timestamped directory. Old attempts are kept (configurable via `--keep`) so you can see progression over time.

---

## Programmatic Usage

```python
from pathlib import Path
from fvf import FVFConfig, GateRunner, load_gates, ReportGenerator

# Load config
config = FVFConfig(api_base_url="http://localhost:9999")

# Load gates from YAML
gates = load_gates(Path("gates.yaml"))

# Run all gates
runner = GateRunner(config, gates)
results = runner.run_all()

# Generate report
generator = ReportGenerator(config.resolved_evidence_dir())
report = generator.generate(results, project_name="My App")
print(generator.to_markdown(report))
```

---

## Trade-offs

Honest assessment of when FVF might not be the right tool:

| Concern | Reality |
|---------|---------|
| **Slower than unit tests** | Yes — real browsers take 2-5s per gate. That's the point. |
| **Requires running services** | Yes. Start your server before running gates. |
| **Non-deterministic** | Possible — idb tap coordinates can miss. Use accessibility tree assertions over pixel coords. |
| **Higher setup cost** | Yes. Initial gate authoring takes 30-60 min. Saves hours of debugging mock-verified bugs. |
| **CI complexity** | Needs Playwright + optional idb. Docker images are available. |

FVF is not a replacement for fast feedback loops during development. It's a gate system that runs before you ship or merge, ensuring the real system works.

---

## Contributing

1. Fork the repo
2. Create a feature branch
3. Add your feature
4. Run the gates (not unit tests — gates)
5. Submit a PR with evidence screenshots

```bash
pip install -e ".[dev]"
fvf validate --gate templates/api-gate.yaml
```

---

## Troubleshooting

### `fvf validate` fails with "no gates found"
Ensure your gate YAML file path is correct: `fvf validate --gate ./gates.yaml`. Use `fvf gate list gates.yaml` to verify the file parses correctly.

### Playwright not installed
Run `playwright install chromium` after `pip install -e .`. The framework uses Playwright for browser-based validation.

### Evidence directory not created
The framework creates the evidence directory automatically. Ensure you have write permissions in the current directory, or specify a custom path: `--evidence-dir /path/to/evidence`.

### Gate passes locally but fails in CI
CI environments may not have browsers installed. Use `fvf validate --gate gates.yaml --type api` for API-only gates, or ensure Playwright browsers are installed in your CI image.

### Screenshots are blank or black
Increase the wait time in your gate configuration. The default screenshot delay may not be enough for pages with async content loading.

## License

MIT © krzemienski
