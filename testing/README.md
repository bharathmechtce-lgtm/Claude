# Testing

All test scripts, benchmark data, and results live here. Organized by purpose.

## Structure

```
testing/
  scenarios/          # Test scripts that run evaluations
  benchmarks/         # Test datasets, ground truth, scenario definitions
  eval/               # Evaluation harness scripts (scoring/comparison)
  results/            # Timestamped output from test runs (with log)
```

## Folders

### `scenarios/`
Python scripts that execute tests against the bot:
- `benchmark_eval.py` - Single-message order extraction accuracy
- `conversational_test.py` - Multi-turn conversation flow testing
- `run_all_haiku.py` - Full benchmark suite with Haiku model
- `two_agent_sim.py` - Two-agent (customer+bot) simulation
- `retest_failures.py` - Re-run previously failed cases
- `build_*.py` - Scripts to generate benchmark datasets

### `benchmarks/`
Static test data and ground truth:
- `test_scenarios.json` - Scenario definitions
- `product_catalog.json` - Product catalog for testing
- `HIL_Benchmark.xlsx` - Human-in-the-loop benchmark
- `Conversation_Benchmark.xlsx` - Conversation test cases
- `ground_truth_benchmark.xlsx` - Ground truth for scoring

### `eval/`
Scoring and evaluation harness:
- `eval_harness.py` / `eval_harness_v2.py` - Compare model output vs ground truth
- `run_eval.bat` - Windows batch script for running evaluation

### `results/`
Every test run produces timestamped output here. See `results/README.md` for the full log with descriptions of what was run, why, and outcomes.
