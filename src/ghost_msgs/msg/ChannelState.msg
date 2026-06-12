# Aggregated state of one control channel.

string channel              # e.g. "spot/drive"
geometry_msgs/Twist fused   # the command published to the robot this tick
OperatorState[] operators   # every operator currently known on this channel
