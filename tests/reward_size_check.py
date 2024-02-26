import time

import PyDAQmx as daq
import numpy as np


class DigitalOutput:

    def __init__(self, chan):
        self.chan = chan
        self.task = daq.Task()
        self.task.CreateDOChan(chan, "", daq.DAQmx_Val_GroupByChannel)

    def write_uint(self, value):
        value_arr = np.array(value, dtype=np.uint8)
        self.task.WriteDigitalLines(
            1, 1, -1, daq.DAQmx_Val_GroupByChannel, value_arr, None, None
        )

    def start(self):
        self.write_uint(1)

    def stop(self):
        self.write_uint(0)

    def close(self):
        # Call the ClearTask method to release the port
        if self.task is not None:
            print('closing digital output')
            self.task.ClearTask()
            self.task = None


valveController = DigitalOutput('Dev1/port0/line1')

repeat = 1000
print('starting test. repeating for {} times'.format(repeat))
for i in range(repeat):
    valveController.start()
    time.sleep(0.2)
    valveController.stop()
    time.sleep(0.5)

print('done')
