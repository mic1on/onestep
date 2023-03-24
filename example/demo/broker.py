from onestep.broker import MemoryBroker

mb = MemoryBroker()

for _ in range(10):
    mb.send("hello world")

for message in mb.consume():
    print(message)
