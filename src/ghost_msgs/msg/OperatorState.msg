# One operator's contribution to a channel, as computed by the aggregator.

string operator_id
geometry_msgs/Twist twist   # the operator's most recent input on this channel
float32 weight              # aggregation weight applied this tick, 0..1
float32 age                 # seconds since this input arrived at the aggregator
bool active                 # false once the input is older than the staleness window
