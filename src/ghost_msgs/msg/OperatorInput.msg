# One operator's control contribution for one channel.
# Published by every client to the shared topic /operators/input;
# the aggregator is the sole subscriber.

std_msgs/Header header      # stamped by the client at input time, not arrival
string operator_id          # stable per-client identifier
string channel              # target channel, e.g. "spot/drive" or "spot2/drive"
geometry_msgs/Twist twist
