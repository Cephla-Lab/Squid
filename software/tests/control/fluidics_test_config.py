"""One simulated-instrument FluidicsConfig for the test suite.

The real file is the operator's own (machine_configs/fluidics_config.yaml, gitignored,
copied from the instrument's standalone fluidics installation); the conftest fixture
writes this YAML to a temporary file instead of shipping an example config in the repo."""

CONFIG_YAML = """\
config_version: '2.0'
microcontroller:
  serial_number: '15579610'
syringe_pump:
  serial_number: A9TLZRF2
  volume_ul: 5000
  ports_allowed:
  - 1
  - 2
  - 3
  waste_port: 3
  extract_port: 2
  dispense_port: null
  speed_code_limit: 10
reagent_selection:
  selector_valves:
    valve_ids:
    - 0
    - 1
    - 2
    number_of_ports:
      0: 10
      1: 10
      2: 10
    tubing_fluid_amount_to_valve_ul:
      0: 0
      1: 200
      2: 340
    name_mapping:
      port_1: reagent x
      port_2: y
      port_3: z
      port_4: a
      port_5: b
      port_6: c
      port_7: t
      port_8: x
      port_9: y
      port_10: z
      port_11: aa
      port_12: bb
      port_13: cc
      port_14: dd
      port_15: ee
      port_16: ff
      port_17: gg
      port_18: hh
      port_19: ii
      port_20: j
      port_21: k
      port_22: l
      port_23: m
      port_24: n
      port_25: buffer 1
      port_26: buffer 2
      port_27: buffer 3
      port_28: buffer 4
    tubing_fluid_amount_ul:
      port_1: 700
      port_2: 700
      port_3: 700
      port_4: 700
      port_5: 700
      port_6: 700
      port_7: 700
      port_8: 700
      port_9: 700
      port_10: 600
      port_11: 600
      port_12: 600
      port_13: 600
      port_14: 600
      port_15: 600
      port_16: 600
      port_17: 600
      port_18: 600
      port_19: 450
      port_20: 450
      port_21: 450
      port_22: 450
      port_23: 450
      port_24: 450
      port_25: 700
      port_26: 700
      port_27: 900
      port_28: 900
  common_tubing_fluid_amount_ul: 800
# Optional -- omit the whole section if no flow sensor is fitted.
#
# index is the sensor's physical position on the board, which is its I2C bus
# and fixes which packet bytes carry it: 1 = Wire1 = bytes 23-24, 2 = Wire2 =
# bytes 25-26. Bus 0 is not supported; it carries the selector valves, whose
# transactions would stall flow reads during a valve move.
#
# One or two sensors, in any combination. A single sensor may sit at index 2
# with nothing at index 1 -- it reads bytes 25-26 and 23-24 report no sensor.
# Note only index 1 feeds the firmware's closed-loop flow control and the
# CLEAR_LINES guard; a sensor at index 2 is read and plotted but not fed back.
flow_sensors:
- index: 1
  name: syringe_draw
  # Draw protection. Keep it quoted -- bare off is a YAML boolean.
  #   off   read and plot only
  #   warn  log a flow fault and carry on
  #   stop  halt the draw and fail the sequence
  # This is the starting mode; the GUI switches it per sensor at runtime. Start
  # a new setup on "warn" and watch what the rule would have fired on before
  # letting it halt a run. All four fields are optional; shown at their defaults.
  monitor: "off"
  ramp_up_seconds: 3.0
  tolerance_fraction: 0.3
  max_flow_rate_ul_min: 2000
application: Flow Cell
"""

TEC_CONFIG_YAML = CONFIG_YAML + "\ntemperature_controller:\n  serial_number: SIM-TEC\n  channels: 2\n"

# A plate-sipper rig, as Squid-Fluidics ships it (sample_config/plate_sipper_config.yaml): one 10-port
# valve, the sipper on port 1, bottles on ports 2-6 (4 and 5 volume tracked), and an XYZ stage over
# two 96-well plates, probes (the default plate) and ssct. Kept here rather than read from the
# submodule so re-teaching the sample does not move these tests.
SIPPER_CONFIG_YAML = """\
config_version: '2.0'
microcontroller:
  serial_number: '20021310'
syringe_pump:
  serial_number: A9DVHMOCA
  volume_ul: 5000
  ports_allowed:
  - 1
  - 2
  - 3
  waste_port: 1
  extract_port: 2
  dispense_port: null
  speed_code_limit: 10
reagent_selection:
  selector_valves:
    valve_ids:
    - 0
    number_of_ports:
      0: 10
    tubing_fluid_amount_to_valve_ul:
      0: 0
    name_mapping:
      port_1: sipper
      port_2: wash buffer 1
      port_3: 4x SSC
      port_4: DAPI
      port_5: anti-bleaching buffer
      port_6: wash buffer 2
    tubing_fluid_amount_ul:
      port_1: 460
      port_2: 200
      port_3: 220
      port_4: 240
      port_5: 240
      port_6: 410
    loaded_volume_ul:
      port_4: 2000
      port_5: 5000
  common_tubing_fluid_amount_ul: 310
  nimotion_gantry_system:
    selector_valve_port: 1
    push_volume_ul: 710
    serial_number: A10PIMTHA
    baudrate: 115200
    axes: {x: X1, y: Y2, z: Z3}
    travel_z_mm: 7.2
    xy_velocity_mm_s: 50
    z_velocity_mm_s: 20
    homing_velocity_mm_s: 20
    homing_acceleration_mm_s2: 200
    default_reservoir: probes
    reservoirs:
      1:
        name: probes
        type: plate
        number_of_wells: 96
        a1_xy_mm: [8.8, 1.3]
        dip_z_mm: 54.2
        column_axis: "+y"
        row_axis: "+x"
        loaded_ul: 350
      2:
        name: ssct
        type: plate
        number_of_wells: 96
        a1_xy_mm: [102.5, 1.5]
        dip_z_mm: 50.0
        column_axis: "+y"
        row_axis: "+x"
        loaded_ul: 1800
    priming_well: ssct/H11
    clean_up_well: ssct/H12
    agitate_amplitude_mm: 5.0
flow_sensors:
- index: 1
  name: syringe_draw
  monitor: "off"
application: Flow Cell
"""

# Two seqFISH rounds on that rig (Squid-Fluidics' sample_sequences/seqfish-plate-sipper.yaml, trimmed):
# hyb draws from a probes well, push moves it into the flow cell from the paired ssct well, ssct washes
# from that same well after three dips, then a bottle row.
SIPPER_ROWS = [
    {"type": "priming", "round": "setup", "fluidic_port": 3, "flow_rate": 5000, "volume": 800},
    {
        "type": "flow_reagent",
        "round": "R01",
        "name": "hyb",
        "fluidic_port": 1,
        "well": "A1",
        "flow_rate": 2000,
        "volume": 100,
    },
    {"type": "push", "round": "R01", "name": "push", "well": "ssct/A1", "flow_rate": 2000, "incubation_time": 20},
    {
        "type": "flow_reagent",
        "round": "R01",
        "name": "ssct",
        "fluidic_port": 1,
        "well": "ssct/A1",
        "flow_rate": 5000,
        "volume": 1000,
        "dip_cycles": 3,
    },
    {
        "type": "flow_reagent",
        "round": "R01",
        "name": "dapi",
        "fluidic_port": 4,
        "flow_rate": 5000,
        "volume": 300,
        "fill_tubing_with": 3,
    },
]


def sipper_protocol():
    """A protocol of SIPPER_ROWS, each row a fresh copy."""
    from control.models.fluidics_protocol import ProtocolFile

    return ProtocolFile(name="sipper", sequences=[dict(row) for row in SIPPER_ROWS])
