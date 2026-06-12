# ghost_aggregator

Fuses N simultaneous operator input streams into a single command stream per
robot. This node is the only commander: operator clients never publish to the
robot topics directly.

```
operator clients ──publish──▶ /operators/input (ghost_msgs/OperatorInput)
                                     │
                                     ▼ (sole subscriber)
                            operator_aggregator
                              │ strategy: passthrough | select | mean
                              │ velocity clamps + staleness watchdog
                              ├──▶ /spot/cmd_vel, /spot2/cmd_vel (geometry_msgs/Twist)
                              └──▶ /ui_state (ghost_msgs/UiState)
                                     ▲
operator clients ◀──subscribe────────┘  (display only — weights, fused command)
```

Inputs are addressed to a *channel* (`<robot>/drive`, e.g. `spot/drive`), so
different operators can occupy different channels and fusion happens
per-channel. Identity travels in the message (`operator_id`) on one shared
topic rather than per-operator topics, since ROS 2 has no wildcard
subscriptions.

## Running

```bash
ros2 launch ghost_aggregator operator_aggregator.launch.py
```

Strategies can be switched live:

```bash
ros2 param set /operator_aggregator strategy select
ros2 param set /operator_aggregator selected_operator alice
```

- `passthrough` — most recent active input wins (last-writer-wins). With one
  operator this reproduces single-client behavior.
- `select` — manual switching: one-hot on `selected_operator`.
- `mean` — all active operators averaged equally.

New strategies go in `ghost_aggregator/strategies.py`; they assign
per-operator weights and the node resolves the weighted mean.

## Parameters

| name | default | |
|---|---|---|
| `robots` | `[spot, spot2]` | one `<robot>/drive` channel and `/<robot>/cmd_vel` publisher each |
| `tick_rate` | `10.0` | fusion/publish rate (Hz) |
| `input_timeout` | `0.5` | seconds before an input stops carrying weight |
| `operator_forget_after` | `5.0` | seconds of silence before an operator leaves `/ui_state` |
| `max_linear_speed` | `0.3` | m/s, planar norm clamp on the fused command |
| `max_angular_speed` | `0.2` | rad/s clamp |
| `idle_zero_ticks` | `3` | explicit zero-twists published when a channel goes idle, before going silent |
| `strategy` | `passthrough` | see above |
| `selected_operator` | `""` | operator id for `select` |

## Session logging

```bash
ros2 run ghost_aggregator record_session.sh [output_dir]
```

Bags every operator input (with issue-time stamps), the UI state (which
records the weights actually applied), the fused commands, and TF.
