# Test Results

Every test run should be logged here with a clear record of **what was run, why, what the results were, and when**.

## Naming Convention

Results files follow this pattern:
```
<test_type>_<YYYYMMDD>_<HHMMSS>.<ext>
```

Examples:
- `benchmark_results_20260301_135936.json`
- `Conv_Test_Results_20260301_155027.xlsx`
- `full_haiku_results_20260302_042643.json`

## Results Log

Each test run should have an entry below (most recent first):

### Template
```
| Date       | Time     | Test Type          | Script Used                  | What Was Tested                          | Why It Was Run                           | Result Summary                           | Files                                    |
|------------|----------|--------------------|------------------------------|------------------------------------------|------------------------------------------|------------------------------------------|------------------------------------------|
| YYYY-MM-DD | HH:MM:SS | benchmark/conv/etc | testing/scenarios/script.py  | Description of what was tested           | Reason for running this test             | PASS/FAIL - brief summary                | result_file.json                         |
```

---

## Results History

| Date       | Time     | Test Type      | Script Used                              | What Was Tested                                              | Why It Was Run                                    | Result Summary                                                    | Files                                              |
|------------|----------|----------------|------------------------------------------|--------------------------------------------------------------|---------------------------------------------------|-------------------------------------------------------------------|----------------------------------------------------|
| 2026-03-02 | 05:50:00 | Two-Agent Sim  | scenarios/two_agent_sim.py               | Two-agent (customer+bot) conversation simulation             | End-to-end conversation flow testing              | See two_agent_results_20260302_055000.json                        | two_agent_results_20260302_055000.json              |
| 2026-03-02 | 04:51:06 | Full Haiku     | scenarios/run_all_haiku.py               | Full benchmark suite with Haiku model                        | Model performance evaluation                      | See full_haiku_results_20260302_045106.json                       | full_haiku_results_20260302_045106.json             |
| 2026-03-02 | 04:26:43 | Full Haiku     | scenarios/run_all_haiku.py               | Full benchmark suite with Haiku model                        | Iterative model tuning                            | See full_haiku_results_20260302_042643.json                       | full_haiku_results_20260302_042643.json             |
| 2026-03-02 | 04:00:57 | Full Haiku     | scenarios/run_all_haiku.py               | Full benchmark suite with Haiku model                        | Iterative model tuning                            | See full_haiku_results_20260302_040057.json                       | full_haiku_results_20260302_040057.json             |
| 2026-03-02 | 03:40:25 | Full Haiku     | scenarios/run_all_haiku.py               | Full benchmark suite with Haiku model                        | Iterative model tuning                            | See full_haiku_results_20260302_034025.json                       | full_haiku_results_20260302_034025.json             |
| 2026-03-01 | 22:02:58 | Full Haiku     | scenarios/run_all_haiku.py               | Full benchmark suite with Haiku model                        | Baseline Haiku evaluation                         | See full_haiku_results_20260301_220258.json                       | full_haiku_results_20260301_220258.json             |
| 2026-03-01 | 21:23:27 | Full Haiku     | scenarios/run_all_haiku.py               | Full benchmark suite with Haiku model                        | Initial Haiku run                                 | See full_haiku_results_20260301_212327.json                       | full_haiku_results_20260301_212327.json             |
| 2026-03-01 | 20:51:59 | Retest         | scenarios/retest_failures.py             | Re-running failed test cases                                 | Fix verification after initial failures           | See retest_results_20260301_205159.json                           | retest_results_20260301_205159.json                 |
| 2026-03-01 | 20:45:32 | Retest         | scenarios/retest_failures.py             | Re-running failed test cases                                 | Fix verification after initial failures           | See retest_results_20260301_204532.json                           | retest_results_20260301_204532.json                 |
| 2026-03-01 | 15:50:27 | Conversation   | scenarios/conversational_test.py         | Multi-turn conversational order flow                         | Validate conversation handling                    | See Conv_Test_Results_20260301_155027.xlsx                        | Conv_Test_Results_20260301_155027.xlsx              |
| 2026-03-01 | 13:59:36 | Benchmark      | scenarios/benchmark_eval.py              | Order extraction accuracy benchmark                          | Baseline model accuracy measurement               | See benchmark_results_20260301_135936.json                        | benchmark_results_20260301_135936.json              |

---

## How to Add a New Result

1. Run your test script (it should auto-generate a timestamped output file)
2. Move/copy the output file to this `testing/results/` folder
3. Add a row to the Results History table above
4. Commit with a message like: `test: add benchmark results from YYYY-MM-DD run`
