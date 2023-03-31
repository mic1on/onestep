from blinker import signal

started = signal("started")
stopped = signal("stopped")

message_received = signal("message_received")
message_sent = signal("message_sent")
message_consumed = signal("message_consumed")
message_error = signal("message_error")
message_drop = signal("message_drop")
