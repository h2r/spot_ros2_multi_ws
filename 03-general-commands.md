# **💬 General Commands**

**Imitation Learning \+ Recordings**

| ./merge\_datasets.sh \--name \<name\> | Merges recordings from a given folder into a single recording |
| :---- | :---- |
| ./replay\_bag \<file\> \--spot-name \<spot\> \--speed \<int\> | Replays a recording onto a spot at a given speed |
| ./run\_policy.sh \--checkpoint /ros2\_ws/runs/… \--spot \<spot/spot2\> \--device \<cpu/gpu\> | Deploys a given policy file onto a robot |
| ./train\_policy.sh \--dataset \<name\> \--steps \<N\> \--batch-size \<b\> | Trains an ACT policy using data from the merged recordings file |

**General ROS Server Commands (Helpful if new to ROS)**

| colcon build \--packages-select \<package\> | Build a specific package, but not its dependencies |
| :---- | :---- |
| colcon build \--packages-up-to \<package\> | Build a specific package and its dependency chain |
| colcon build \--symlink-install | Builds container by symlinking files (allows you to edit files without rebuilding) |
| tmux kill-server | Stops the current ROS server |
| Ctrl B \+ D | Hide current panes |

**Launch Commands**

| /launch\_multi\_spot.sh | Launch for all spots, requires all to be on to localize |
| :---- | :---- |
| /launch\_multi\_spot.sh \--spot \<spot\> | Launch a single spot (e.g., spot, spot2), requires that spot to be on to localize |

## **1\. launch\_multi\_spot.sh**

Boots the whole tmux session: both Spot drivers, the ROS\# bridge, multi-robot coordination, the recorder (listener\_node or dual\_listener\_node, chosen automatically), and an idle "Replay" pane.

```shell
./launch_multi_spot.sh                # both robots, dual_listener_node in the recording pane
./launch_multi_spot.sh --spot spot     # single-robot mode, listener_node
```

## **2\. set\_recording\_session.sh**

Sets which subfolder under recordings/ new recordings save into — takes effect on the *next* recording, no restart needed. Single-robot goes to recordings/\<name\>/, dual-robot goes to recordings/\<name\>\_dual/.

```shell
./set_recording_session.sh --name demo
./set_recording_session.sh              # show current session
./set_recording_session.sh --clear      # back to flat recordings/
```

## **3\. replay\_bag.sh**

Replays a single-robot recording onto one Spot — auto sit/stand \+ arm-positioning, then plays the recorded actions. Prompts "type yes" before moving anything; \--dry-run skips that since nothing moves.

```shell
./replay_bag.sh 20260727_175555 --spot-name spot
./replay_bag.sh 20260727_175555 --dry-run
```

## **4\. replay\_dual\_bag.sh**

Same idea, for two robots at once — mentions both robots in the confirmation prompt. \--folder points it at a named recordings subfolder (taken literally, e.g. demo\_dual), since dual recordings aren't in the flat root.

```shell
./replay_dual_bag.sh dualbag_20260804_014112 --folder demo_dual --dry-run
./replay_dual_bag.sh dualbag_20260804_014112 --folder demo_dual
```

## **5\. merge\_datasets.sh**

Combines multiple single-episode recordings into one multi-episode training dataset under merged\_recordings/. \--from scopes it to a session folder; folders ending in \_dual auto-switch to the dual filename pattern and get \_dual appended to the output name.

```shell
./merge_datasets.sh --name plushie_pickups --from plushie_recording
./merge_datasets.sh --name demo --from demo_dual
```

## **6\. train\_policy.sh**

Wraps lerobot-train — points it at a folder in merged\_recordings/ or recordings/ and trains a policy, with wandb logging on by default.

```shell
./train_policy.sh --dataset plushie_pickups --steps 40000
```

## **7\. run\_policy.sh**

Deploys a trained checkpoint to run live on a robot. Real deployment is the default, gated by a "type yes" prompt (same pattern as the replay scripts); \--dry-run logs predictions without publishing them, no prompt needed.

```shell
./run_policy.sh --checkpoint /ros2_ws/recordings/runs/<run>/checkpoints/<step>/pretrained_model --spot-name spot
./run_policy.sh --checkpoint ... --spot-name spot2 --dry-run
```

