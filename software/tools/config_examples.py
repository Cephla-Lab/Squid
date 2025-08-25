import abc
from typing import List, Tuple, Literal, Union, Optional
import re

import pydantic


class Objective(pydantic.BaseModel):
    @staticmethod
    def calculate_pixel_size_factor(objective, tube_lens_mm):
        """pixel_size_um = sensor_pixel_size * binning_factor * lens_factor"""
        magnification = objective["magnification"]
        objective_tube_lens_mm = objective["tube_lens_f_mm"]
        lens_factor = objective_tube_lens_mm / magnification / tube_lens_mm
        return lens_factor

    # TODO: Add validations to make sure mag, na, tube lens are all >0, etc.  That way users will be warned
    # of their mistakes on startup!
    name: str
    magnification: float
    NA: float
    tube_lens_f_mm: float

    def get_pixel_size_factor(self, tube_lens_mm):
        return self.tube_lens_f_mm / self.magnification / tube_lens_mm


class Objectives(pydantic.BaseModel):
    objectives: List[Objective]

class SampleFormat(pydantic.BaseModel):
    name: str
    origin_x_mm: float
    origin_y_mm: float

    def get_sample_centers(self) -> List[Tuple[float, float]]:
        raise NotImplementedError("Subclasses must implement this")

class GlassSlideFormat(SampleFormat):
    type: Literal["GlassSlideFormat"] = "GlassSlideFormat"

    count: int
    spacing_mm: float
    slide_x_mm: float
    slide_y_mm: float

    def get_sample_centers(self):
        return [(self.origin_x_mm + n * self.spacing_mm + self.slide_x_mm / 2.0, self.origin_y_mm + self.slide_y_mm / 2.0) for n in range(self.count)]

class WellSampleFormat(SampleFormat):
    type: Literal["WellSampleFormat"] = "WellSampleFormat"
    well_size_mm: float
    well_spacing_mm: float
    # NOTE(imo): What is number of skip for?
    number_of_skip: float
    rows: int
    cols: int

    def get_well_center(self, well_name):
        pattern = r"([A-Za-z]+)(\d+)"

        def row_to_index(row):
            index = 0
            for char in row:
                index = index * 26 + (ord(char.upper()) - ord("A") + 1)
            return index - 1

        def index_to_row(index):
            index += 1
            row = ""
            while index > 0:
                index -= 1
                row = chr(index % 26 + ord("A")) + row
                index //= 26
            return row

        match = re.match(pattern, well_name)
        if not match:
            raise ValueError(f"Invalid well name: '{well_name}'")

        row_letter, col_number = match.groups()
        row = row_to_index(row_letter)
        col = int(col_number) - 1

        if row < 0 or col < 0:
            raise ValueError(f"Invalid well name: '{well_name}'")

        return (self.origin_x_mm + self.well_spacing_mm * col + self.well_size_mm / 2.0,
                self.origin_y_mm + self.well_spacing_mm * row + self.well_size_mm / 2.0)

    def get_sample_centers(self):
        # NOTE(imo): This assume swell size is the diameter equivalent, so to get to the center is well size /2
        return [(self.origin_x_mm + n * self.well_spacing_mm + self.well_size_mm / 2.0,
                 self.origin_y_mm + m * self.well_spacing_mm + self.well_size_mm / 2.0) for m in range(self.rows)
                for n in range(self.cols)]

class SampleFormats(pydantic.BaseModel):
    sample_formats: List[Union[GlassSlideFormat, WellSampleFormat, SampleFormat]]

class LaserAutoFocusSettings(pydantic.BaseModel):
    x_offset: float
    y_offset: float
    calibration_timestamp: str
    # etc, etc, etc ...

class ChannelConfiguration(pydantic.BaseModel):
    name: str
    exposure_time: float
    analog_gain: float
    # etc, etc, etc ...
    # Also, instead of looking at the name to tell properties we should define them
    # on the model, or the illumination type, or something.  We just shouldn't match on
    # the name like we do in a bunch of places now.  EG:
    is_laser: bool = False

class ObjectiveProfileParameters(pydantic.BaseModel):
    laser_autofocus_settings: Optional[LaserAutoFocusSettings]
    channel_configurations: List[ChannelConfiguration]

class UserProfile(pydantic.BaseModel):
    name: str
    objective_parameters: dict[str, ObjectiveProfileParameters]

class UserProfiles(pydantic.BaseModel):
    profiles: List[UserProfile]

if __name__ == "__main__":
    profiles_yaml = """
profiles:
    - name: "Default"
      objective_parameters:
          2X:
              laser_autofocus_settings:
                  x_offset: 1.0
                  y_offset: 2.0
                  calibration_timestamp: "Some time"
              channel_configurations:
                  - name: 405nm for 2X
                    exposure_time: 2
                    analog_gain: 10
                    is_laser: True
                  - name: BF for 2X
                    exposure_time: 10
                    analog_gain: 0
                    is_laser: False
          10x:
              laser_autofocus_settings:
              channel_configurations:
                  - name: 488nm for 10x
                    exposure_time: 200
                    analog_gain: 4
                    is_laser: True
    - name: "Ian's Custom Profile"
      objective_parameters:
          2x:
              laser_autofocus_settings:
              channel_configurations:
                  - name: 638nm for 2x
                    exposure_time: 123
                    analog_gain: 9
                    is_laser: True
                  - name: BF for 2x
                    exposure_time: 1
                    analog_gain: 0
                    is_laser: True
    """
    objectives_yaml = """
objectives:
    - name: 2X
      magnification: 2.0
      NA: 0.1
      tube_lens_f_mm: 180
    - name: 10x
      magnification: 10.0
      NA: 0.3
      tube_lens_f_mm: 180
    """

    sample_formats_yaml = """
sample_formats:
    - name: 4 Glass Slides
      type: GlassSlideFormat
      origin_x_mm: 0
      origin_y_mm: 0
      count: 4
      spacing_mm: 20
      slide_x_mm: 20
      slide_y_mm: 80
    - name: 96 Well Plate
      type: WellSampleFormat
      origin_x_mm: 11.31
      origin_y_mm: 10.75
      well_size_mm: 6.21
      well_spacing_mm: 9
      number_of_skip: 8
      rows: 16
      cols: 25
    """

    # You'll need pydantic-yaml for this
    import pydantic_yaml

    deserialized_profiles = pydantic_yaml.parse_yaml_raw_as(UserProfiles, profiles_yaml)
    deserialized_objectives = pydantic_yaml.parse_yaml_raw_as(Objectives, objectives_yaml)
    deserialized_formats = pydantic_yaml.parse_yaml_raw_as(SampleFormats, sample_formats_yaml)

    assert isinstance(deserialized_formats.sample_formats[0], GlassSlideFormat)
    assert isinstance(deserialized_formats.sample_formats[1], WellSampleFormat)

    serialized_user_profiles = pydantic_yaml.to_yaml_str(deserialized_profiles)
    serialized_objectives = pydantic_yaml.to_yaml_str(deserialized_objectives)
    serialized_sample_formats = pydantic_yaml.to_yaml_str(deserialized_formats)

    print(f"Serialized user profiles:\n{serialized_user_profiles}")
    print(f"Serialized objectives:\n{serialized_objectives}")
    print(f"Serialized sample formats:\n{serialized_sample_formats}")
