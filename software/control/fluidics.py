import pandas as pd
import threading
from typing import Dict, Optional
import sys

sys.path.append("fluidics_v2/software")

from fluidics.control.controller import FluidControllerSimulation, FluidController
from fluidics.control.syringe_pump import SyringePumpSimulation, SyringePump
from fluidics.control.selector_valve import SelectorValveSystem
from fluidics.control.disc_pump import DiscPump
from fluidics.control.temperature_controller import TCMControllerSimulation, TCMController
from fluidics.merfish_operations import MERFISHOperations
from fluidics.open_chamber_operations import OpenChamberOperations
from fluidics.experiment_worker import ExperimentWorker
from fluidics.control._def import CMD_SET

import json


class Fluidics:
    def __init__(self, config_path: str, simulation: bool = False, callbacks: Optional[Dict] = None):
        """Initialize the fluidics runner

        Args:
            config_path: Path to the configuration JSON file
            simulation: Whether to run in simulation mode
            callbacks: Optional dictionary of callback functions
        """
        self.config_path = config_path
        self.simulation = False
        self.port_list = None
        self.available_port_names = None

        # Initialize member variables
        self.config = None
        self.sequences = None
        self.sequences_before_imaging = None
        self.sequences_after_imaging = None
        self.controller = None
        self.syringe_pump = None
        self.selector_valve_system = None
        self.disc_pump = None
        self.temperature_controller = None
        self.experiment_ops = None
        self.worker = None
        self.thread = None

        # Set default callbacks if none provided
        self.callbacks = callbacks or {
            "update_progress": lambda idx, seq_num, status: print(f"Sequence {idx} ({seq_num}): {status}"),
            "on_error": lambda msg: print(f"Error: {msg}"),
            "on_finished": lambda: print("Experiment completed"),
            "on_estimate": lambda time, n: print(f"Est. time: {time}s, Sequences: {n}"),
        }

        self._load_config()

    def initialize(self):
        # Initialize everything
        self._initialize_hardware()
        self._initialize_control_objects()

    def _load_config(self):
        """Load configuration from JSON file"""
        with open(self.config_path, "r") as f:
            self.config = json.load(f)

        available_ports = 0
        for id, num_ports in self.config["selector_valves"]["number_of_ports"].items():
            if int(id) in self.config["selector_valves"]["valve_ids_allowed"]:
                available_ports += int(num_ports) - 1
        available_ports += 1

        self.available_port_names = []
        for i in range(1, available_ports + 1):
            self.available_port_names.append(
                "Port " + str(i) + ": " + self.config["selector_valves"]["reagent_name_mapping"]["port_" + str(i)]
            )
        print(self.available_port_names)

    def _initialize_hardware(self):
        """Initialize hardware controllers based on simulation mode"""
        if self.simulation:
            self.controller = FluidControllerSimulation(self.config["microcontroller"]["serial_number"])
            self.syringe_pump = SyringePumpSimulation(
                sn=self.config["syringe_pump"]["serial_number"],
                syringe_ul=self.config["syringe_pump"]["volume_ul"],
                speed_code_limit=self.config["syringe_pump"]["speed_code_limit"],
                waste_port=3,
            )
            if (
                "temperature_controller" in self.config
                and self.config["temperature_controller"]["use_temperature_controller"]
            ):
                self.temperature_controller = TCMControllerSimulation()
        else:
            self.controller = FluidController(self.config["microcontroller"]["serial_number"])
            self.syringe_pump = SyringePump(
                sn=self.config["syringe_pump"]["serial_number"],
                syringe_ul=self.config["syringe_pump"]["volume_ul"],
                speed_code_limit=self.config["syringe_pump"]["speed_code_limit"],
                waste_port=3,
            )
            if (
                "temperature_controller" in self.config
                and self.config["temperature_controller"]["use_temperature_controller"]
            ):
                self.temperature_controller = TCMController(self.config["temperature_controller"]["serial_number"])

        self.controller.begin()
        self.controller.send_command(CMD_SET.CLEAR)

    def _initialize_control_objects(self):
        """Initialize valve system and operation objects"""
        self.selector_valve_system = SelectorValveSystem(self.controller, self.config)

        if self.config["application"] == "Open Chamber":
            self.disc_pump = DiscPump(self.controller)
            self.experiment_ops = OpenChamberOperations(
                self.config, self.syringe_pump, self.selector_valve_system, self.disc_pump
            )
        else:  # MERFISH
            self.experiment_ops = MERFISHOperations(self.config, self.syringe_pump, self.selector_valve_system)

    def get_syringe_pump_volume(self):
        """Get the volume of the syringe pump"""
        return self.syringe_pump.volume_ul

    def load_sequences(self, sequence_path: str):
        """Load and filter sequences from CSV file"""
        df = pd.read_csv(sequence_path)
        for col in df.columns:
            try:
                # Try to convert to Int64 (pandas extension type that supports NaN)
                if df[col].dtype.kind in "fi":  # float or int
                    df[col] = df[col].astype("Int64")
            except:
                pass
        # Keep sequences that are either included or are imaging steps
        mask = (df["include"] == 1) | (df["sequence_name"] == "Imaging")
        self.sequences = df[mask].reset_index(drop=True)

        # Find indices before and after imaging
        imaging_idx = self.sequences[self.sequences["sequence_name"] == "Imaging"].index
        if len(imaging_idx) > 0:
            imaging_idx = imaging_idx[0]
            self.sequences_before_imaging = slice(0, imaging_idx)
            self.sequences_after_imaging = slice(imaging_idx + 1, len(self.sequences))
        else:
            self.sequences_before_imaging = slice(0, len(self.sequences))
            self.sequences_after_imaging = slice(0, 0)
        return self.sequences.copy()

    def run_before_imaging(self):
        """Run the sequences before imaging"""
        self.run_sequences(self.sequences.iloc[self.sequences_before_imaging])

    def run_after_imaging(self):
        """Run the sequences after imaging"""
        self.run_sequences(self.sequences.iloc[self.sequences_after_imaging])

    def priming(self, ports: list, last_port: int, volume_ul: int, flow_rate: int = 5000):
        """Priming the fluidics system"""
        # Create priming sequence dataframe
        priming_seq = pd.DataFrame(
            {
                "sequence_name": ["Priming"],
                "fluidic_port": [last_port],
                "flow_rate": [flow_rate],
                "volume": [volume_ul],
                "incubation_time": [0],
                "repeat": [1],
                "fill_tubing_with": [0],
                "include": [1],
                "use_ports": [ports],
            }
        )
        self.run_sequences(priming_seq)

    def clean_up(self, ports: list, last_port: int, volume_ul: int, repeat: int, flow_rate: int = 10000):
        """Clean up the fluidics system"""
        cleanup_seq = pd.DataFrame(
            {
                "sequence_name": ["Clean Up"],
                "fluidic_port": [last_port],
                "flow_rate": [flow_rate],
                "volume": [volume_ul],
                "incubation_time": [0],
                "repeat": [repeat],
                "fill_tubing_with": [0],
                "include": [1],
                "use_ports": [ports],
            }
        )
        self.run_sequences(cleanup_seq)

    def manual_flow(self, port: int, flow_rate: int, volume_ul: int):
        """Manually flow from a specific port at a specific flow rate for a specific volume"""
        flow_seq = pd.DataFrame(
            {
                "sequence_name": ["Flow Manually"],
                "fluidic_port": [port],
                "flow_rate": [flow_rate],
                "volume": [volume_ul],
                "incubation_time": [0],
                "repeat": [1],
                "fill_tubing_with": [0],
                "include": [1],
            }
        )
        self.run_sequences(flow_seq)

    def run_before_imaging(self):
        """Run the sequences before imaging"""
        self.run_sequences(self.sequences.iloc[self.sequences_before_imaging])

    def run_after_imaging(self):
        """Run the sequences after imaging"""
        self.run_sequences(self.sequences.iloc[self.sequences_after_imaging])

    def run_sequences(self, sequences: pd.DataFrame):
        """Start running the sequences in a separate thread"""
        self.worker = ExperimentWorker(self.experiment_ops, sequences, self.config, self.callbacks)
        self.thread = threading.Thread(target=self.worker.run)
        self.thread.start()

    def emergency_stop(self):
        """Stop syringe pump operation immediately"""
        self.syringe_pump.abort()
        self.worker.abort()

    def reset_abort(self):
        self.syringe_pump.reset_abort()

    def wait_for_completion(self):
        """Wait for the sequence thread to complete"""
        if self.thread:
            self.thread.join()

    def update_port(self, index: int):
        """Update the fluidics port for Flow Reagent sequences

        Args:
            port: New port number to use for Flow Reagent sequences with port <= 24
        """
        # Find Flow Reagent sequences with port <= 24
        mask = (self.sequences["sequence_name"] == "Flow Probe") & (self.sequences["fluidic_port"] <= 24)

        self.sequences.loc[mask, "fluidic_port"] = self.port_list[index]

    def set_rounds(self, rounds: list):
        """Rounds: a list of port indices of unique reagents to run"""
        self.port_list = rounds

    def close(self):
        """Clean up hardware resources"""
        if self.syringe_pump:
            self.syringe_pump.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
