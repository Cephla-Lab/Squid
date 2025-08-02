import abc
import multiprocessing
import queue
import os
import time
import threading
from typing import Optional, Generic, TypeVar, Dict, Any, List, Tuple
from uuid import uuid4
from pathlib import Path
from datetime import timedelta
from dataclasses import dataclass, field

import imageio as iio
import numpy as np
from tifffile import tifffile

from control import _def, utils_acquisition
import squid.abc
import squid.logging
from control.utils_config import ChannelMode

# OME-TIFF support imports
try:
    import tifffile
    TIFFFILE_AVAILABLE = True
except ImportError:
    TIFFFILE_AVAILABLE = False

# Constants for OME-TIFF
IMAGEJ_AXIS_ORDER = "tzcyxs"


# NOTE(imo): We want this to be fast.  But pydantic does not support numpy serialization natively, which means
# that we need a custom serializer (which will be slow!).  So, use dataclass here instead.
@dataclass
class CaptureInfo:
    position: squid.abc.Pos
    z_index: int
    capture_time: float
    configuration: ChannelMode
    save_directory: str
    file_id: str
    region_id: int
    fov: int
    configuration_idx: int


@dataclass()
class JobImage:
    image_array: Optional[np.array]


T = TypeVar("T")


@dataclass
class Job(abc.ABC, Generic[T]):
    capture_info: CaptureInfo
    capture_image: JobImage

    job_id: str = field(default_factory=lambda: str(uuid4()))

    def image_array(self) -> np.array:
        if self.capture_image.image_array is not None:
            return self.capture_image.image_array

        raise NotImplementedError("Only np array JobImages are supported right now.")

    @abc.abstractmethod
    def run(self) -> T:
        raise NotImplementedError("You must implement run for your job type.")


@dataclass
class JobResult(Generic[T]):
    job_id: str
    result: Optional[T]
    exception: Optional[Exception]


class SaveImageJob(Job):
    def __init__(self, capture_info: CaptureInfo, capture_image: JobImage):
        super().__init__(capture_info=capture_info, capture_image=capture_image)
        self._log = squid.logging.get_logger(__class__.__name__)

    def run(self) -> bool:
        is_color = len(self.image_array().shape) > 2
        return self.save_image(self.image_array(), self.capture_info, is_color)

    def save_image(self, image: np.array, info: CaptureInfo, is_color: bool):
        # Handle OME-TIFF saving - NOTE: OME-TIFF is handled in the main process, not here
        if _def.FILE_SAVING_OPTION == _def.FileSavingOption.OME_TIFF:
            # OME-TIFF saving is handled in main process via _image_callback
            # This should only happen if OME-TIFF fallback occurs
            self._log.info("OME-TIFF fallback: saving as individual image")
            # Fall through to individual image saving
        
        # Handle multi-page TIFF saving
        elif _def.FILE_SAVING_OPTION == _def.FileSavingOption.MULTI_PAGE_TIFF:
            metadata = {
                "z_level": info.z_index,
                "channel": info.configuration.name,
                "channel_index": info.configuration_idx,
                "region_id": info.region_id,
                "fov": info.fov,
                "x_mm": info.position.x_mm,
                "y_mm": info.position.y_mm,
                "z_mm": info.position.z_mm,
            }
            output_path = os.path.join(
                info.save_directory, f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}_stack.tiff"
            )
            with tifffile.TiffWriter(output_path, append=True) as tiff_writer:
                tiff_writer.write(image, metadata=metadata)
            return True
        
        # Individual images (default and fallback)
        saved_image = utils_acquisition.save_image(
            image=image,
            file_id=info.file_id,
            save_directory=info.save_directory,
            config=info.configuration,
            is_color=is_color,
        )

        if _def.MERGE_CHANNELS:
            # TODO(imo): Add this back in
            raise NotImplementedError("Image merging not supported yet")

        return True


# These are debugging jobs - they should not be used in normal usage!
class HangForeverJob(Job):
    def run(self) -> bool:
        while True:
            time.sleep(1)

        return True  # noqa


class ThrowImmediatelyJobException(RuntimeError):
    pass


class ThrowImmediatelyJob(Job):
    def run(self) -> bool:
        raise ThrowImmediatelyJobException("ThrowImmediatelyJob threw")


class JobRunner(multiprocessing.Process):
    def __init__(self):
        super().__init__()
        self._log = squid.logging.get_logger(__class__.__name__)

        self._input_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._input_timeout = 1.0
        self._output_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._shutdown_event: multiprocessing.Event = multiprocessing.Event()

    def dispatch(self, job: Job):
        self._input_queue.put_nowait(job)

        return True

    def output_queue(self) -> multiprocessing.Queue:
        return self._output_queue

    def has_pending(self):
        return not self._input_queue.empty()

    def shutdown(self, timeout_s=1.0):
        self._shutdown_event.set()
        self.join(timeout=timeout_s)

    def run(self):
        while not self._shutdown_event.is_set():
            job = None
            try:
                job = self._input_queue.get(timeout=self._input_timeout)
                self._log.info(f"Running job {job.job_id}...")
                result = job.run()
                self._log.info(f"Job {job.job_id} returned. Sending result to output queue.")
                self._output_queue.put_nowait(JobResult(job_id=job.job_id, result=result, exception=None))
                self._log.debug(f"Result for {job.job_id} is on output queue.")
            except queue.Empty:
                pass
            except Exception as e:
                if job:
                    self._log.exception(f"Job {job.job_id} failed! Returning exception result.")
                    self._output_queue.put_nowait(JobResult(job_id=job.job_id, result=None, exception=e))
        self._log.info("Shutdown request received, exiting run.")


class _NULL:
    """Sentinel for missing metadata (OME-TIFF pattern)."""
    pass

_NULL = _NULL()


class _5DWriterBase:
    """Base class for 5D OME-TIFF writer architecture."""
    
    def __init__(self) -> None:
        self.current_sequence: Optional['MDASequence'] = None
        self.position_sizes: List[Dict[str, int]] = []
        self._arrays: Dict[str, np.memmap] = {}
    
    def sequenceStarted(self, seq: 'MDASequence', meta: Any = _NULL) -> None:
        """Initialize sequence."""
        self.current_sequence = seq
        self.position_sizes = [seq.sizes.copy()]
    
    def write_frame(self, ary: np.memmap, index: tuple, frame: np.ndarray) -> None:
        """Write a frame to the file."""
        ary[index] = frame
    
    def new_array(self, position_key: str, dtype: np.dtype, sizes: Dict[str, int]) -> np.memmap:
        """Create new array - to be implemented by subclasses."""
        raise NotImplementedError


class MDASequence:
    """Minimal MDA sequence to match professional interface patterns."""
    
    def __init__(self, sizes: Dict[str, int], pixel_size: float, params: dict, channels: List[str]):
        self.sizes = sizes
        self.pixel_size = pixel_size
        self.params = params
        self.channels = channels
        
        # Create plan objects for metadata
        self.time_plan = self._create_time_plan() if params.get('dt(s)') else None
        self.z_plan = self._create_z_plan() if params.get('dz(um)') else None
    
    def _create_time_plan(self):
        """Create time plan object for metadata."""
        class TimePlan:
            def __init__(self, interval):
                self.interval = interval
        return TimePlan(self.params['dt(s)'])
    
    def _create_z_plan(self):
        """Create Z plan object for metadata."""
        class ZPlan:
            def __init__(self, step):
                self.step = step
        return ZPlan(self.params['dz(um)'])


class OMETiffWriter(_5DWriterBase):
    """Professional OME-TIFF writer following established patterns."""

    def __init__(self, filename: Path | str) -> None:
        if not TIFFFILE_AVAILABLE:
            raise ImportError(
                "tifffile is required for OME-TIFF support. "
                "Please install with: pip install tifffile"
            )

        self._filename = str(filename)
        if not self._filename.endswith((".tiff", ".tif")):
            raise ValueError("filename must end with '.tiff' or '.tif'")
        self._is_ome = ".ome.tif" in self._filename

        super().__init__()

    def sequenceStarted(self, seq: 'MDASequence', meta: Any = _NULL) -> None:
        """Initialize sequence with proper axis ordering."""
        super().sequenceStarted(seq, meta)
        # Non-OME (ImageJ) hyperstack axes MUST be in TZCYXS order
        if not self._is_ome:
            self.position_sizes = [
                {k: x[k] for k in IMAGEJ_AXIS_ORDER if k.lower() in x}
                for x in self.position_sizes
            ]

    def write_frame(self, ary: np.memmap, index: tuple, frame: np.ndarray) -> None:
        """Write a frame to the file with proper flushing."""
        super().write_frame(ary, index, frame)
        ary.flush()

    def new_array(self, position_key: str, dtype: np.dtype, sizes: Dict[str, int]) -> np.memmap:
        """Create a new tifffile and memmap for this position."""
        dims, shape = zip(*sizes.items())

        metadata: Dict[str, Any] = self._sequence_metadata()
        metadata["axes"] = "".join(dims).upper()

        # Append position key to filename if multiple positions
        if (seq := self.current_sequence) and seq.sizes.get("p", 1) > 1:
            ext = ".ome.tif" if self._is_ome else ".tif"
            fname = self._filename.replace(ext, f"_{position_key}{ext}")
        else:
            fname = self._filename

        # Create parent directories if needed
        Path(fname).parent.mkdir(parents=True, exist_ok=True)
        
        # Write empty file to disk
        tifffile.imwrite(
            fname,
            shape=shape,
            dtype=dtype,
            metadata=metadata,
            imagej=not self._is_ome,
            ome=self._is_ome,
        )

        # Create memory-mapped array
        mmap = tifffile.memmap(fname, dtype=dtype)
        # Preserve singleton dimensions
        mmap.shape = shape

        return mmap

    def _sequence_metadata(self) -> Dict[str, Any]:
        """Create comprehensive metadata for the sequence."""
        if not self._is_ome:
            return {}

        metadata: Dict[str, Any] = {}
        
        if seq := self.current_sequence:
            # Time metadata
            if seq.time_plan and hasattr(seq.time_plan, "interval"):
                interval = seq.time_plan.interval
                if isinstance(interval, timedelta):
                    interval = interval.total_seconds()
                metadata["TimeIncrement"] = interval
                metadata["TimeIncrementUnit"] = "s"
            
            # Z metadata
            if seq.z_plan and hasattr(seq.z_plan, "step"):
                metadata["PhysicalSizeZ"] = seq.z_plan.step
                metadata["PhysicalSizeZUnit"] = "µm"
            
            # Channel metadata
            if seq.channels:
                metadata["Channel"] = {"Name": [f"Channel_{c}" for c in seq.channels]}
            
            # Physical pixel sizes
            metadata["PhysicalSizeX"] = seq.pixel_size
            metadata["PhysicalSizeY"] = seq.pixel_size
            metadata["PhysicalSizeXUnit"] = "µm"
            metadata["PhysicalSizeYUnit"] = "µm"

        return metadata


class OMETiffManager:
    """Thread-safe manager for OME-TIFF writers across the acquisition."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._writers: Dict[str, OMETiffWriter] = {}
        self._arrays: Dict[str, np.memmap] = {}
        self._sequences: Dict[str, MDASequence] = {}
        self._log = squid.logging.get_logger(self.__class__.__name__)
        # Use class-level lock to avoid redundancy
    
    def get_writer_key(self, info: CaptureInfo) -> str:
        """Generate unique key for writer based on acquisition parameters."""
        return f"{info.region_id}_{info.fov}"
    
    def initialize_acquisition(self, base_info: CaptureInfo, acquisition_params: Dict[str, Any]):
        """Initialize OME-TIFF writers for the acquisition."""
        with self.__class__._lock:
            writer_key = self.get_writer_key(base_info)
            
            if writer_key in self._writers:
                return  # Already initialized
            
            try:
                self._log.info(f"Initializing OME-TIFF for {writer_key} with params: {acquisition_params}")
                
                # Create sequence metadata with defensive checks
                channels = [config.name for config in acquisition_params.get('selected_configurations', [])]
                
                # Ensure no None values in sizes
                nt = acquisition_params.get('Nt', 1)
                nz = acquisition_params.get('NZ', 1)
                image_height = acquisition_params.get('image_height', 512)
                image_width = acquisition_params.get('image_width', 512)
                
                self._log.info(f"Raw values: Nt={nt}, NZ={nz}, height={image_height}, width={image_width}")
                
                sizes = {
                    't': nt if nt is not None else 1,
                    'z': nz if nz is not None else 1,
                    'c': len(channels) if channels else 1,
                    'y': image_height if image_height is not None else 512,
                    'x': image_width if image_width is not None else 512
                }
                
                # Final validation - ensure no None values
                for key, value in sizes.items():
                    if value is None:
                        self._log.error(f"Size dimension '{key}' is None! Setting to 1")
                        sizes[key] = 1
                
                self._log.info(f"Final sizes: {sizes}, channels: {channels}")
                
                # Calculate pixel size with defensive checks
                sensor_pixel_size_um = acquisition_params.get('sensor_pixel_size_um', 3.45)
                objective_info = acquisition_params.get('objective', {})
                objective_mag = objective_info.get('magnification', 10) if objective_info else 10
                tube_lens_mm = acquisition_params.get('tube_lens_mm', 50)
                obj_tube_lens_mm = objective_info.get('tube_lens_f_mm', 200) if objective_info else 200
                
                # Ensure all values are not None and non-zero
                sensor_pixel_size_um = sensor_pixel_size_um if sensor_pixel_size_um is not None and sensor_pixel_size_um > 0 else 3.45
                objective_mag = objective_mag if objective_mag is not None and objective_mag > 0 else 10
                tube_lens_mm = tube_lens_mm if tube_lens_mm is not None and tube_lens_mm > 0 else 50
                obj_tube_lens_mm = obj_tube_lens_mm if obj_tube_lens_mm is not None and obj_tube_lens_mm > 0 else 200
                
                # Calculate pixel size with zero-division protection
                denominator = objective_mag * (tube_lens_mm / obj_tube_lens_mm)
                pixel_size = sensor_pixel_size_um / denominator if denominator > 0 else 3.45
                self._log.info(f"Calculated pixel size: {pixel_size} μm (sensor: {sensor_pixel_size_um}, mag: {objective_mag}, tube: {tube_lens_mm}, obj_tube: {obj_tube_lens_mm})")
                
                # Create sequence
                sequence = MDASequence(sizes, pixel_size, acquisition_params, channels)
                self._log.info(f"Created MDASequence successfully")
                self._sequences[writer_key] = sequence
                
                # Create output filename
                output_file = Path(base_info.save_directory) / f"{writer_key}.ome.tif"
                
                # Create writer
                writer = OMETiffWriter(output_file)
                writer.sequenceStarted(sequence)
                self._writers[writer_key] = writer
                
                # Create memory-mapped array
                position_sizes = writer.position_sizes[0]
                mmap = writer.new_array("0", np.uint16, position_sizes)  # Default to uint16
                self._arrays[writer_key] = mmap
                
                self._log.info(f"Initialized OME-TIFF writer for {writer_key}: {output_file}")
                
            except (ValueError, TypeError, OSError, IOError) as e:
                self._log.error(f"Failed to initialize OME-TIFF writer for {writer_key}: {e}")
                raise
            except Exception as e:
                self._log.error(f"Unexpected error initializing OME-TIFF writer for {writer_key}: {e}")
                raise
    
    def write_frame(self, info: CaptureInfo, image: np.ndarray, time_point: int = 0):
        """Write a frame to the appropriate OME-TIFF file."""
        with self.__class__._lock:
            writer_key = self.get_writer_key(info)
            
            if writer_key not in self._writers:
                self._log.error(f"No OME-TIFF writer found for {writer_key}")
                return False
            
            try:
                writer = self._writers[writer_key]
                mmap = self._arrays[writer_key]
                sequence = self._sequences[writer_key]
                
                # Build index tuple based on position_sizes order
                position_sizes = writer.position_sizes[0]
                index_values = {
                    't': time_point,
                    'z': info.z_index,
                    'c': info.configuration_idx
                }
                
                index = []
                for dim in position_sizes.keys():
                    if dim.lower() in index_values:
                        index.append(index_values[dim.lower()])
                
                # Write frame
                writer.write_frame(mmap, tuple(index), image)
                
                return True
                
            except (ValueError, TypeError, OSError, IOError) as e:
                self._log.error(f"Failed to write frame for {writer_key}: {e}")
                return False
            except Exception as e:
                self._log.error(f"Unexpected error writing frame for {writer_key}: {e}")
                return False
    
    def finalize_acquisition(self, writer_key: str = None):
        """Finalize and cleanup OME-TIFF writers."""
        with self.__class__._lock:
            keys_to_cleanup = [writer_key] if writer_key else list(self._writers.keys())
            
            for key in keys_to_cleanup:
                try:
                    if key in self._arrays:
                        self._arrays[key].flush()  # Flush before deletion
                        del self._arrays[key]
                    if key in self._writers:
                        del self._writers[key]
                    if key in self._sequences:
                        del self._sequences[key]
                    self._log.info(f"Finalized OME-TIFF writer for {key}")
                except Exception as e:
                    self._log.error(f"Error finalizing OME-TIFF writer for {key}: {e}")
    
    def cleanup_all(self):
        """Cleanup all OME-TIFF writers."""
        self.finalize_acquisition()


class SaveOMETiffJob(Job):
    """Professional OME-TIFF saving job that integrates with the existing architecture."""
    
    def __init__(self, capture_info: CaptureInfo, capture_image: JobImage):
        super().__init__(capture_info=capture_info, capture_image=capture_image)
        self._log = squid.logging.get_logger(__class__.__name__)

    def run(self) -> bool:
        """Execute OME-TIFF saving job."""
        try:
            manager = OMETiffManager()
            
            # Extract time point from file_id or use default
            time_point = self._extract_time_point()
            
            # Write frame to OME-TIFF
            success = manager.write_frame(self.capture_info, self.image_array(), time_point)
            
            if not success:
                self._log.warning(f"Failed to write OME-TIFF frame for {self.capture_info.file_id}")
            
            return success
            
        except Exception as e:
            self._log.error(f"OME-TIFF job failed for {self.capture_info.file_id}: {e}")
            return False
    
    def _extract_time_point(self) -> int:
        """Extract time point from the save directory path."""
        try:
            # Extract time point from path like "/path/to/experiment/001/"
            path_parts = Path(self.capture_info.save_directory).parts
            for part in reversed(path_parts):
                if part.isdigit():
                    return int(part)
            return 0
        except:
            return 0
