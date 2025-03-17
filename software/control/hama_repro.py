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

sent_trigger = threading.Event()
frame_count = 0
keep_running = threading.Event()
keep_running.set()


def read_function(rf_camera):
    global frame_count
    while keep_running.is_set():
        frame_ready = rf_camera.wait_event(DCAMWAIT_CAPEVENT.FRAMEREADY, 1)
        if frame_ready:
            frame = rf_camera.buf_getlastframedata()
            sent_trigger.clear()
            frame_count += 1
        time.sleep(0.001)  # yield so we don't spin on the cpu


thread = threading.Thread(target=read_function, args=[camera])
thread.start()

last_count = frame_count
last_trigger = 0
try:
    while True:
        overdue = time.time() - last_trigger > 10 * exposure_time_s + 0.1
        if not sent_trigger.is_set() or overdue:
            if overdue:
                log.info(f"overdue!")
                transfer_info = camera.cap_transferinfo()
                log.info(f"Transfer info: {transfer_info}")
                cap_status = camera.cap_status()
                log.info(f"Cap status: {cap_status}")
            log.info("Sending trigger.")
            camera.cap_firetrigger()
            log.info("Trigger sent.")
            last_trigger = time.time()
            sent_trigger.set()
        time.sleep(exposure_time_s)
        log.info(f"Current frame id: {frame_count}")
except KeyboardInterrupt:
    keep_running.clear()
