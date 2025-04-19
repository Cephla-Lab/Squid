import logging
import threading
import time
from control.dcam import Dcam, Dcamapi
from control.dcamapi4 import *

_baseline_log_format = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"
_baseline_log_dateformat = "%Y-%m-%d %H:%M:%S"
log = logging.getLogger("hama_repro")
logging.basicConfig(level=logging.INFO, format=_baseline_log_format, datefmt=_baseline_log_dateformat)
exposure_time_s = 0.001

Dcamapi.init()
camera = Dcam(0)
camera.dev_open(0)
camera.prop_setvalue(DCAM_IDPROP.TRIGGERSOURCE, DCAMPROP.TRIGGERSOURCE.SOFTWARE)
camera.prop_setvalue(DCAM_IDPROP.EXPOSURETIME, exposure_time_s)
camera.buf_alloc(5)
camera.cap_start()

frame_count = 0
last_count = frame_count
last_trigger = 0
trigger_sent = False
while True:
    if not trigger_sent or time.time() - last_trigger > 1:
        camera.cap_firetrigger()
        log.info("Trigger sent.")
        trigger_sent = True
        last_trigger = time.time()
    frame_ready = camera.wait_event(DCAMWAIT_CAPEVENT.FRAMEREADY, 1)
    if frame_ready:
        log.info(f"Frame ready, {time.time() - last_trigger} since trigger.")
        frame = camera.buf_getlastframedata()
        trigger_sent = False
        frame_count += 1
    time.sleep(exposure_time_s)
    log.info(f"Current frame id: {frame_count}")
