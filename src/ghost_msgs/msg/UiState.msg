# Curated display state, published by the aggregator on /ui_state.
# This is the only topic operator front-ends subscribe to; it exists
# solely to be rendered (per-operator inputs, weights, fused commands).

std_msgs/Header header
ChannelState[] channels
