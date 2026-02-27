#!/usr/bin/env python3
"""
Test hardware trigger for multi-camera Toupcam systems.

This script directly enumerates cameras and tests hardware triggers
without relying on INI or YAML configuration files.

Usage:
    cd software
    python tools/test_multi_camera_hw_trigger.py
"""

import sys
import time
import argparse
import logging
import io

sys.path.insert(0, ".")

# Suppress logs during imports
logging.basicConfig(level=logging.ERROR)
_stdout, _stderr = sys.stdout, sys.stderr
sys.stdout = sys.stderr = io.StringIO()

try:
    import control.toupcam as toupcam
    from control.microcontroller import Microcontroller, get_microcontroller_serial_device
    from control.camera_toupcam import ToupcamCamera
    from squid.config import CameraConfig, CameraVariant, CameraPixelFormat, ToupcamCameraModel
    from squid.abc import CameraAcquisitionMode
finally:
    sys.stdout, sys.stderr = _stdout, _stderr

for name in ["squid", "control", ""]:
    lg = logging.getLogger(name)
    lg.setLevel(logging.ERROR)
    lg.handlers.clear()


def test_mapping(cameras, mcu, channel_mapping, num_frames, illumination_time_us, timeout):
    """Test a specific camera-to-channel mapping."""
    results = {sn: {"captured": 0, "failed": 0, "trigger_ch": ch} for sn, ch in channel_mapping.items()}

    for i in range(num_frames):
        for sn, camera in cameras.items():
            trigger_ch = channel_mapping[sn]

            mcu.send_hardware_trigger(
                control_illumination=True,
                illumination_on_time_us=illumination_time_us,
                trigger_output_ch=trigger_ch,
            )

            start_time = time.time()
            frame = None
            while time.time() - start_time < timeout:
                frame = camera.read_frame()
                if frame is not None:
                    break
                time.sleep(0.01)

            if frame is not None:
                results[sn]["captured"] += 1
            else:
                results[sn]["failed"] += 1
                print(f"  TIMEOUT: {sn[:12]}... frame {i+1}")

            time.sleep(0.05)

        if (i + 1) % 10 == 0 or i == num_frames - 1:
            print(f"  Progress: {i+1}/{num_frames} frames")

    return results


def main():
    parser = argparse.ArgumentParser(description="Test hardware trigger for multi-camera Toupcam systems")
    parser.add_argument("--exposure-ms", type=float, default=50.0, help="Exposure time in ms")
    parser.add_argument("--num-frames", type=int, default=5, help="Number of frames per camera")
    parser.add_argument("--timeout", type=float, default=0.5, help="Frame timeout in seconds")
    args = parser.parse_args()

    print("Multi-Camera Hardware Trigger Test (standalone)")
    print("=" * 50)

    # Enumerate cameras directly
    devices = toupcam.Toupcam.EnumV2()
    if len(devices) < 2:
        print(f"ERROR: Need at least 2 cameras, found {len(devices)}")
        return 1

    print(f"Found {len(devices)} camera(s):")
    serial_numbers = []
    for i, dev in enumerate(devices):
        print(f"  [{i}] {dev.id} ({dev.displayname})")
        serial_numbers.append(dev.id)

    # Initialize microcontroller
    print("\nInitializing microcontroller...", end=" ")
    sys.stdout.flush()
    try:
        mcu = Microcontroller(serial_device=get_microcontroller_serial_device())
        mcu.initialize_drivers()
        print("OK")
    except Exception as e:
        print(f"FAILED: {e}")
        return 1

    # Create trigger/strobe functions
    def make_trigger_fn(ch):
        def fn(illumination_time_ms):
            us = int(illumination_time_ms * 1000) if illumination_time_ms else 0
            mcu.send_hardware_trigger(True, us, ch)
            return True

        return fn

    def make_strobe_fn(ch):
        def fn(strobe_delay_ms):
            mcu.set_strobe_delay_us(int(strobe_delay_ms * 1000), ch)
            return True

        return fn

    # Open cameras using ToupcamCamera
    print("Opening cameras...", end=" ")
    sys.stdout.flush()
    cameras = {}
    try:
        for i, sn in enumerate(serial_numbers[:2]):  # First 2 cameras
            config = CameraConfig(
                camera_type=CameraVariant.TOUPCAM,
                camera_model=ToupcamCameraModel.ITR3CMOS26000KMA,
                serial_number=sn,
                default_pixel_format=CameraPixelFormat.MONO16,
                default_binning=(1, 1),
                default_fan_speed=1,
                default_temperature=20.0,
                default_black_level=3,
            )
            camera = ToupcamCamera(
                config=config,
                hw_trigger_fn=make_trigger_fn(i),
                hw_set_strobe_delay_ms_fn=make_strobe_fn(i),
            )
            camera.set_exposure_time(args.exposure_ms)
            camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
            camera.start_streaming()
            cameras[sn] = camera
        print("OK")
    except Exception as e:
        print(f"FAILED: {e}")
        for c in cameras.values():
            c.close()
        return 1

    time.sleep(0.5)

    # Test both mappings
    sn0, sn1 = serial_numbers[0], serial_numbers[1]
    illumination_time_us = int(args.exposure_ms * 1000)

    print(f"\n=== Mapping 1: Cam0 -> Ch0, Cam1 -> Ch1 ===")
    mapping1 = {sn0: 0, sn1: 1}
    results1 = test_mapping(cameras, mcu, mapping1, args.num_frames, illumination_time_us, args.timeout)

    print(f"\n=== Mapping 2: Cam0 -> Ch1, Cam1 -> Ch0 ===")
    mapping2 = {sn0: 1, sn1: 0}
    results2 = test_mapping(cameras, mcu, mapping2, args.num_frames, illumination_time_us, args.timeout)

    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)

    def print_results(results, label):
        print(f"\n{label}:")
        all_pass = True
        for sn, r in results.items():
            status = "PASS" if r["failed"] == 0 else "FAIL"
            if r["failed"] > 0:
                all_pass = False
            print(f"  {sn[:16]}... (ch={r['trigger_ch']}): {r['captured']}/{args.num_frames} - {status}")
        return all_pass

    all_passed_1 = print_results(results1, "Mapping 1 (Cam0->Ch0, Cam1->Ch1)")
    all_passed_2 = print_results(results2, "Mapping 2 (Cam0->Ch1, Cam1->Ch0)")

    print("\n" + "-" * 50)
    if all_passed_1 and not all_passed_2:
        print(f"CORRECT MAPPING:")
        print(f"  {sn0} -> Channel 0")
        print(f"  {sn1} -> Channel 1")
    elif all_passed_2 and not all_passed_1:
        print(f"CORRECT MAPPING:")
        print(f"  {sn0} -> Channel 1")
        print(f"  {sn1} -> Channel 0")
    elif all_passed_1 and all_passed_2:
        print("BOTH MAPPINGS WORK")
    else:
        print("NEITHER MAPPING WORKS - check hardware wiring")

    # Cleanup
    for camera in cameras.values():
        camera.stop_streaming()
        camera.close()

    return 0 if (all_passed_1 or all_passed_2) else 1


if __name__ == "__main__":
    sys.exit(main())
