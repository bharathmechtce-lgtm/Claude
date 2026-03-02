# Test Results

Every test run logged with **what was run, why, results, and timestamp**.

## Results History

| Date       | Time     | Test Type      | Script                           | What Was Tested                              | Why                                    | Result Summary                              | File                                          |
|------------|----------|----------------|----------------------------------|----------------------------------------------|----------------------------------------|---------------------------------------------|-----------------------------------------------|
| 2026-03-02 | 05:50:00 | Two-Agent Sim  | two_agent_sim.py                 | Two-agent conversation simulation            | End-to-end flow testing                | See JSON                                    | two_agent_results_20260302_055000.json         |
| 2026-03-02 | 04:51:06 | Full Haiku     | run_all_haiku.py                 | Full benchmark with Haiku                    | Model evaluation                       | See JSON                                    | full_haiku_results_20260302_045106.json        |
| 2026-03-02 | 04:26:43 | Full Haiku     | run_all_haiku.py                 | Full benchmark with Haiku                    | Iterative tuning                       | See JSON                                    | full_haiku_results_20260302_042643.json        |
| 2026-03-02 | 04:00:57 | Full Haiku     | run_all_haiku.py                 | Full benchmark with Haiku                    | Iterative tuning                       | See JSON                                    | full_haiku_results_20260302_040057.json        |
| 2026-03-02 | 03:40:25 | Full Haiku     | run_all_haiku.py                 | Full benchmark with Haiku                    | Iterative tuning                       | See JSON                                    | full_haiku_results_20260302_034025.json        |
| 2026-03-01 | 22:02:58 | Full Haiku     | run_all_haiku.py                 | Full benchmark with Haiku                    | Baseline evaluation                    | See JSON                                    | full_haiku_results_20260301_220258.json        |
| 2026-03-01 | 21:23:27 | Full Haiku     | run_all_haiku.py                 | Full benchmark with Haiku                    | Initial Haiku run                      | See JSON                                    | full_haiku_results_20260301_212327.json        |
| 2026-03-01 | 20:51:59 | Retest         | retest_failures.py               | Re-running failed cases                      | Fix verification                       | See JSON                                    | retest_results_20260301_205159.json            |
| 2026-03-01 | 20:45:32 | Retest         | retest_failures.py               | Re-running failed cases                      | Fix verification                       | See JSON                                    | retest_results_20260301_204532.json            |
| 2026-03-01 | 15:50:27 | Conversation   | conversational_test.py           | Multi-turn conversation flow                 | Validate conversation handling         | See XLSX                                    | Conv_Test_Results_20260301_155027.xlsx         |
| 2026-03-01 | 13:59:36 | Benchmark      | benchmark_eval.py                | Order extraction accuracy                    | Baseline accuracy measurement          | See JSON                                    | benchmark_results_20260301_135936.json         |

## How to Add a New Result

1. Run your test script (auto-generates timestamped output)
2. Move output to `testing/results/`
3. Add a row to the table above
