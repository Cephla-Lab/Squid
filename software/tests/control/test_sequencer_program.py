"""Tests for control.sequencer_program — the host mirror of the firmware wire contract.

The authority for every number here is firmware/controller/src/sequencer/seq_wire.h,
seq_types.h and seq_engine.h.  TestHeaderCrossCheck parses those headers directly so a
change on either side breaks this suite instead of the microscope.
"""

import re
import struct
from pathlib import Path

import pytest

import control.sequencer_program as sp
from control.sequencer_program import (
    Order,
    ProgramValidationError,
    SeqCameraSpec,
    SeqChannelSpec,
    SeqError,
    SeqLoopSpec,
    SeqState,
    SequencerProgram,
    SequencerStatus,
    StackAxisType,
    TriggerMode,
    crc16_ccitt_false,
    program_bytes,
    split_words,
    unpack,
)

# The golden program, byte for byte.  This is the same program the firmware's own wire test
# stages (firmware/controller/test/test_seq_wire/test_seq_wire.cpp::build(buf, 2, 1)), so the
# firmware side can assert the identical bytes.  It was written out by hand from the field
# offsets in seq_wire.h / seq_types.h, NOT by calling pack().
#
#   header  @0  : version=1, n_cameras=1, reserved=0, wait_timeout_us=5_000_000
#   loop    @8  : Piezo, axis 7, dz=120, n_layers=10, ChannelsInner, z_settle=20_000,
#                 return_to_start=1, n_channels=2                       (+1 pad byte @23)
#   ch0     @24 : filter_wheel=NONE, filter_target=0, ttl=0x01, led=NONE, dac=0,
#                 intensity=1000, exposure=10_000, camera_mask=1, z_offset=0, flags=0
#   ch1     @44 : ... ttl=0x02, dac=1, intensity=1001, exposure=20_000 ...
#   cam0    @64 : Level, ready_line=0, ready_active_high=1, readout_overlap_safe=1,
#                 strobe_delay=300, readout=25_000, min_trigger_period=0
GOLDEN_HEX = (
    "01010000404b4c00"
    "0107780000000a0000204e0000010200"
    "ff0000000001ff00e80310270000010000000000"
    "ff0000000002ff01e903204e0000010000000000"
    "010001012c010000a861000000000000"
)

FIRMWARE_SRC = Path(sp.__file__).resolve().parents[2] / "firmware" / "controller" / "src"


def golden_program() -> SequencerProgram:
    """The SequencerProgram that must pack() to GOLDEN_HEX."""
    return SequencerProgram(
        loop=SeqLoopSpec(
            stack_axis_type=StackAxisType.PIEZO,
            stack_axis_id=7,
            dz=120,
            n_layers=10,
            order=Order.CHANNELS_INNER,
            z_settle_us=20000,
            return_to_start=True,
        ),
        channels=(
            SeqChannelSpec(exposure_us=10000, camera_mask=1, illum_ttl_mask=0x01, intensity_dac=0, intensity=1000),
            SeqChannelSpec(exposure_us=20000, camera_mask=1, illum_ttl_mask=0x02, intensity_dac=1, intensity=1001),
        ),
        cameras=(
            SeqCameraSpec(
                trigger_mode=TriggerMode.LEVEL,
                ready_line=0,
                ready_active_high=True,
                readout_overlap_safe=True,
                strobe_delay_us=300,
                readout_time_us=25000,
                min_trigger_period_us=0,
            ),
        ),
        wait_timeout_us=5000000,
    )


def minimal_program(**overrides) -> SequencerProgram:
    """A one-channel, one-camera program that validate() accepts."""
    kwargs = dict(
        loop=SeqLoopSpec(
            stack_axis_type=StackAxisType.PIEZO,
            stack_axis_id=0,
            dz=10,
            n_layers=1,
            order=Order.CHANNELS_INNER,
            z_settle_us=1000,
            return_to_start=True,
        ),
        channels=(SeqChannelSpec(exposure_us=5000, camera_mask=1),),
        cameras=(SeqCameraSpec(trigger_mode=TriggerMode.LEVEL, strobe_delay_us=100, readout_time_us=1000),),
        wait_timeout_us=1000000,
    )
    kwargs.update(overrides)
    return SequencerProgram(**kwargs)


class TestStructSizes:
    def test_wire_struct_sizes(self):
        assert sp.WIRE_HEADER_BYTES == 8
        assert sp.SEQ_LOOP_BYTES == 15
        assert sp.SEQ_CHANNEL_BYTES == 20
        assert sp.WIRE_CAMERA_BYTES == 16

    def test_struct_formats_produce_the_pinned_sizes(self):
        assert struct.calcsize(sp.WIRE_HEADER_FORMAT) == sp.WIRE_HEADER_BYTES
        assert struct.calcsize(sp.SEQ_LOOP_FORMAT) == sp.SEQ_LOOP_BYTES
        assert struct.calcsize(sp.SEQ_CHANNEL_FORMAT) == sp.SEQ_CHANNEL_BYTES
        assert struct.calcsize(sp.WIRE_CAMERA_FORMAT) == sp.WIRE_CAMERA_BYTES

    def test_offsets_and_staging_size(self):
        assert sp.LOOP_OFFSET == 8
        assert sp.CHANNELS_OFFSET == 24
        # kStagingBytes from seq_wire.h, asserted as 472 by the firmware's own test.
        assert sp.STAGING_BYTES == 472
        assert sp.STAGING_BYTES % sp.WORD_BYTES == 0
        # the SEQ_WRITE word index is a single byte
        assert sp.STAGING_BYTES // sp.WORD_BYTES <= 255

    def test_program_bytes(self):
        assert program_bytes(2, 1) == 24 + 2 * 20 + 16
        assert program_bytes(1, 1) == 60
        assert program_bytes(16, 8) == sp.STAGING_BYTES

    def test_program_bytes_is_always_word_aligned(self):
        # Every part of the layout is a multiple of 4, so SEQ_WRITE never has to pad a
        # real program.  split_words() still pads, because nothing in the wire contract
        # promises this stays true.
        for n_ch in range(1, sp.MAX_CHANNELS + 1):
            for n_cam in range(1, sp.MAX_CAMERAS + 1):
                assert program_bytes(n_ch, n_cam) % sp.WORD_BYTES == 0


class TestPack:
    def test_golden_bytes(self):
        assert golden_program().pack().hex() == GOLDEN_HEX

    def test_golden_length_is_unpadded_program_bytes(self):
        packed = golden_program().pack()
        assert len(packed) == program_bytes(2, 1) == 80

    def test_roundtrip(self):
        program = golden_program()
        assert unpack(program.pack()) == program

    def test_roundtrip_of_a_maximal_program(self):
        program = SequencerProgram(
            loop=SeqLoopSpec(
                stack_axis_type=StackAxisType.STEPPER,
                stack_axis_id=2,
                dz=-1234,
                n_layers=4000,
                order=Order.Z_INNER,
                z_settle_us=sp.MAX_DURATION_US,
                return_to_start=False,
            ),
            channels=tuple(
                SeqChannelSpec(
                    exposure_us=1000 + i,
                    camera_mask=0xFF,
                    illum_ttl_mask=i,
                    intensity_dac=i % sp.N_DACS,
                    intensity=65535 - i,
                    led_pattern=i,
                    filter_wheel=sp.NONE_ID,
                    filter_target=-(i + 1) * 1000,
                    z_offset=-(i + 1),
                )
                for i in range(sp.MAX_CHANNELS)
            ),
            cameras=tuple(
                SeqCameraSpec(
                    trigger_mode=TriggerMode.EDGE if i % 2 else TriggerMode.LEVEL,
                    ready_line=sp.NONE_ID if i % 2 else i,
                    ready_active_high=bool(i % 2),
                    readout_overlap_safe=not (i % 2),
                    strobe_delay_us=i * 10,
                    readout_time_us=i * 100,
                    min_trigger_period_us=i * 1000,
                )
                for i in range(sp.MAX_CAMERAS)
            ),
            wait_timeout_us=sp.MAX_DURATION_US,
        )
        assert len(program.pack()) == sp.STAGING_BYTES
        assert unpack(program.pack()) == program

    def test_crc16_is_computed_over_the_unpadded_bytes(self):
        program = golden_program()
        assert program.crc16() == crc16_ccitt_false(program.pack())


class TestCrc16:
    def test_check_value(self):
        # The CRC-16/CCITT-FALSE check value, as named in seq_wire.h.
        assert crc16_ccitt_false(b"123456789") == 0x29B1

    def test_empty(self):
        assert crc16_ccitt_false(b"") == 0xFFFF

    def test_is_not_reflected(self):
        # A reflected variant (CRC-16/KERMIT) would give 0x2189 for this input.
        assert crc16_ccitt_false(b"123456789") != 0x2189


class TestWords:
    def test_golden_word_split(self):
        words = golden_program().words()
        assert len(words) == 20
        assert [i for i, _ in words] == list(range(20))
        assert words[0] == (0, bytes.fromhex("01010000"))
        assert words[1] == (1, bytes.fromhex("404b4c00"))
        assert words[-1] == (19, bytes.fromhex("00000000"))

    def test_words_are_absolute_offsets_so_a_resend_is_idempotent(self):
        packed = golden_program().pack()
        for index, data in golden_program().words():
            assert packed[index * 4 : index * 4 + 4] == data

    def test_split_words_pads_to_a_multiple_of_four(self):
        words = split_words(b"\x01\x02\x03\x04\x05")
        assert words == [(0, b"\x01\x02\x03\x04"), (1, b"\x05\x00\x00\x00")]

    def test_split_words_of_empty_is_empty(self):
        assert split_words(b"") == []


class TestValidate:
    def test_accepts_a_minimal_program(self):
        minimal_program().validate()

    def test_accepts_the_golden_program(self):
        golden_program().validate()

    def test_rejects_zero_channels(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=()).validate()
        assert exc.value.error == SeqError.BAD_CHANNEL_COUNT
        assert "channel" in str(exc.value).lower()

    def test_rejects_too_many_channels(self):
        channels = tuple(SeqChannelSpec(exposure_us=100) for _ in range(sp.MAX_CHANNELS + 1))
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=channels).validate()
        assert exc.value.error == SeqError.BAD_CHANNEL_COUNT

    def test_rejects_zero_cameras(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(cameras=()).validate()
        assert exc.value.error == SeqError.BAD_CAMERA

    def test_rejects_too_many_cameras(self):
        cameras = tuple(SeqCameraSpec(trigger_mode=TriggerMode.LEVEL) for _ in range(sp.MAX_CAMERAS + 1))
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(cameras=cameras).validate()
        assert exc.value.error == SeqError.BAD_CAMERA

    def test_rejects_zero_layers(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(loop=minimal_program().loop.replace(n_layers=0)).validate()
        assert exc.value.error == SeqError.BAD_LAYER_COUNT

    def test_rejects_more_frames_than_the_u16_counter(self):
        # frames_fired travels as a u16 in the status packet: 40000 * 2 > 65535.
        program = minimal_program(
            loop=minimal_program().loop.replace(n_layers=40000),
            channels=(SeqChannelSpec(exposure_us=100), SeqChannelSpec(exposure_us=100)),
        )
        with pytest.raises(ProgramValidationError) as exc:
            program.validate()
        assert exc.value.error == SeqError.BAD_LAYER_COUNT
        assert "65535" in str(exc.value)

    def test_accepts_exactly_the_u16_frame_limit(self):
        minimal_program(
            loop=minimal_program().loop.replace(n_layers=65535),
            channels=(SeqChannelSpec(exposure_us=100),),
        ).validate()

    def test_rejects_an_out_of_range_n_layers_for_the_wire(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(loop=minimal_program().loop.replace(n_layers=65536)).validate()
        assert exc.value.error == SeqError.BAD_LAYER_COUNT

    @pytest.mark.parametrize("field", ["z_settle_us"])
    def test_rejects_a_loop_duration_over_the_bound(self, field):
        loop = minimal_program().loop.replace(**{field: sp.MAX_DURATION_US + 1})
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(loop=loop).validate()
        assert exc.value.error == SeqError.BAD_DURATION

    def test_rejects_a_wait_timeout_over_the_bound(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(wait_timeout_us=sp.MAX_DURATION_US + 1).validate()
        assert exc.value.error == SeqError.BAD_DURATION

    @pytest.mark.parametrize("field", ["strobe_delay_us", "readout_time_us", "min_trigger_period_us"])
    def test_rejects_a_camera_duration_over_the_bound(self, field):
        camera = minimal_program().cameras[0].replace(**{field: sp.MAX_DURATION_US + 1})
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(cameras=(camera,)).validate()
        assert exc.value.error == SeqError.BAD_DURATION
        assert exc.value.detail == 0

    def test_rejects_an_exposure_over_the_bound(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=(SeqChannelSpec(exposure_us=sp.MAX_DURATION_US + 1),)).validate()
        assert exc.value.error == SeqError.BAD_EXPOSURE

    def test_rejects_a_zero_exposure(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=(SeqChannelSpec(exposure_us=0),)).validate()
        assert exc.value.error == SeqError.BAD_EXPOSURE
        assert exc.value.detail == 0

    def test_rejects_an_empty_camera_mask(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=(SeqChannelSpec(exposure_us=100, camera_mask=0),)).validate()
        assert exc.value.error == SeqError.BAD_CAMERA

    def test_rejects_a_camera_mask_bit_with_no_camera(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=(SeqChannelSpec(exposure_us=100, camera_mask=0b10),)).validate()
        assert exc.value.error == SeqError.BAD_CAMERA
        assert exc.value.detail == 0

    def test_rejects_an_unknown_stack_axis_type(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(loop=minimal_program().loop.replace(stack_axis_type=7)).validate()
        assert exc.value.error == SeqError.BAD_STACK_AXIS

    def test_rejects_a_stack_axis_id_out_of_range(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(loop=minimal_program().loop.replace(stack_axis_id=sp.N_DACS)).validate()
        assert exc.value.error == SeqError.BAD_STACK_AXIS

    def test_rejects_a_filter_wheel_out_of_range(self):
        channel = SeqChannelSpec(exposure_us=100, filter_wheel=sp.N_AXES)
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=(channel,)).validate()
        assert exc.value.error == SeqError.BAD_CHANNEL

    def test_rejects_an_intensity_dac_out_of_range(self):
        channel = SeqChannelSpec(exposure_us=100, intensity_dac=sp.N_DACS)
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=(channel,)).validate()
        assert exc.value.error == SeqError.BAD_CHANNEL

    def test_reports_the_offending_channel_index_as_detail(self):
        channels = (SeqChannelSpec(exposure_us=100), SeqChannelSpec(exposure_us=0))
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=channels).validate()
        assert exc.value.detail == 1

    def test_rejects_a_field_that_does_not_fit_the_wire(self):
        with pytest.raises(ProgramValidationError) as exc:
            minimal_program(channels=(SeqChannelSpec(exposure_us=100, intensity=65536),)).validate()
        assert exc.value.error == SeqError.BAD_PROGRAM

    def test_pack_validates_first(self):
        with pytest.raises(ProgramValidationError):
            minimal_program(channels=()).pack()


class TestUnpack:
    def test_rejects_a_short_buffer(self):
        with pytest.raises(ProgramValidationError) as exc:
            unpack(bytes(8))
        assert exc.value.error == SeqError.BAD_PROGRAM

    def test_rejects_a_buffer_longer_than_the_staging_area(self):
        with pytest.raises(ProgramValidationError) as exc:
            unpack(bytes(sp.STAGING_BYTES + 4))
        assert exc.value.error == SeqError.BAD_PROGRAM

    def test_rejects_a_bad_version(self):
        data = bytearray(golden_program().pack())
        data[0] = 9
        with pytest.raises(ProgramValidationError) as exc:
            unpack(bytes(data))
        assert exc.value.error == SeqError.BAD_PROGRAM
        assert exc.value.detail == sp.BAD_PROGRAM_VERSION

    def test_rejects_a_lost_chunk(self):
        data = golden_program().pack()
        with pytest.raises(ProgramValidationError) as exc:
            unpack(data[:-4])
        assert exc.value.error == SeqError.BAD_PROGRAM
        assert exc.value.detail == sp.BAD_PROGRAM_MISMATCH

    def test_rejects_zero_cameras(self):
        data = bytearray(golden_program().pack())
        data[1] = 0
        with pytest.raises(ProgramValidationError) as exc:
            unpack(bytes(data))
        assert exc.value.error == SeqError.BAD_CAMERA

    def test_rejects_too_many_cameras(self):
        data = bytearray(golden_program().pack())
        data[1] = sp.MAX_CAMERAS + 1
        with pytest.raises(ProgramValidationError) as exc:
            unpack(bytes(data))
        assert exc.value.error == SeqError.BAD_CAMERA

    def test_rejects_zero_channels(self):
        data = bytearray(golden_program().pack())
        data[sp.LOOP_OFFSET + 14] = 0  # SeqLoop.n_channels
        with pytest.raises(ProgramValidationError) as exc:
            unpack(bytes(data))
        assert exc.value.error == SeqError.BAD_CHANNEL_COUNT

    def test_rejects_more_frames_than_the_u16_counter(self):
        data = bytearray(golden_program().pack())
        struct.pack_into("<H", data, sp.LOOP_OFFSET + 6, 40000)  # SeqLoop.n_layers
        with pytest.raises(ProgramValidationError) as exc:
            unpack(bytes(data))
        assert exc.value.error == SeqError.BAD_LAYER_COUNT

    def test_ignores_the_pad_byte(self):
        data = bytearray(golden_program().pack())
        data[23] = 0xAB
        assert unpack(bytes(data)) == golden_program()


class TestSequencerStatus:
    def test_decodes_the_packed_state_byte(self):
        status = SequencerStatus.from_response_bytes(0xE7, 0, 0, 0)
        assert status.state == SeqState.RETURNING
        assert status.error == SeqError.WAIT_TIMEOUT

    def test_decodes_idle_none(self):
        status = SequencerStatus.from_response_bytes(0x00, 0, 0, 0)
        assert status.state == SeqState.IDLE
        assert status.error == SeqError.NONE
        assert status.frames_fired == 0

    def test_decodes_detail_and_frames_big_endian(self):
        status = SequencerStatus.from_response_bytes(
            (SeqState.FAILED << 5) | SeqError.STACK_OUT_OF_RANGE, 3, 0x01, 0x02
        )
        assert status.state == SeqState.FAILED
        assert status.error == SeqError.STACK_OUT_OF_RANGE
        assert status.detail == 3
        assert status.frames_fired == 0x0102

    def test_every_error_fits_the_five_bit_field(self):
        for error in SeqError:
            assert 0 <= int(error) <= 0x1F

    def test_every_state_fits_the_three_bit_field(self):
        for state in SeqState:
            assert 0 <= int(state) <= 0x07

    def test_keeps_an_unknown_error_code_readable(self):
        # Five bits carry 0..31; the firmware may one day append past 18.  The read thread
        # must not blow up on that.
        status = SequencerStatus.from_response_bytes(0x1F, 0, 0, 0)
        assert status.error == 0x1F
        assert "UNKNOWN" in status.error_name

    def test_terminal_states(self):
        assert SequencerStatus.from_response_bytes(SeqState.DONE << 5, 0, 0, 0).is_terminal
        assert SequencerStatus.from_response_bytes(SeqState.FAILED << 5, 0, 0, 0).is_terminal
        assert not SequencerStatus.from_response_bytes(SeqState.EXPOSING << 5, 0, 0, 0).is_terminal


class TestStepOrder:
    """Mirror of SeqEngine::step_to_layer_channel()."""

    def test_channels_inner(self):
        program = minimal_program(
            loop=minimal_program().loop.replace(n_layers=3, order=Order.CHANNELS_INNER),
            channels=(SeqChannelSpec(exposure_us=1), SeqChannelSpec(exposure_us=2)),
        )
        assert [program.step_to_layer_channel(k) for k in range(6)] == [
            (0, 0),
            (0, 1),
            (1, 0),
            (1, 1),
            (2, 0),
            (2, 1),
        ]

    def test_z_inner(self):
        program = minimal_program(
            loop=minimal_program().loop.replace(n_layers=3, order=Order.Z_INNER),
            channels=(SeqChannelSpec(exposure_us=1), SeqChannelSpec(exposure_us=2)),
        )
        assert [program.step_to_layer_channel(k) for k in range(6)] == [
            (0, 0),
            (1, 0),
            (2, 0),
            (0, 1),
            (1, 1),
            (2, 1),
        ]

    def test_n_frames_is_layers_times_channels(self):
        program = minimal_program(
            loop=minimal_program().loop.replace(n_layers=7),
            channels=(SeqChannelSpec(exposure_us=1), SeqChannelSpec(exposure_us=2)),
        )
        assert program.n_frames == 14


class TestStackRange:
    """Mirror of SeqEngine::stack_range_ok(): the whole run is refused before the first move."""

    def piezo(self, dz=100, n_layers=10, z_offsets=(0,)):
        return minimal_program(
            loop=minimal_program().loop.replace(
                stack_axis_type=StackAxisType.PIEZO, stack_axis_id=0, dz=dz, n_layers=n_layers
            ),
            channels=tuple(SeqChannelSpec(exposure_us=100, z_offset=z) for z in z_offsets),
        )

    def test_accepts_a_stack_inside_the_dac_range(self):
        self.piezo(dz=100, n_layers=10).check_stack_range(0)
        self.piezo(dz=-100, n_layers=10).check_stack_range(65535)

    def test_rejects_a_start_below_zero(self):
        with pytest.raises(ProgramValidationError) as exc:
            self.piezo().check_stack_range(-1)
        assert exc.value.error == SeqError.STACK_OUT_OF_RANGE
        assert exc.value.detail == 0xFF  # the start position itself

    def test_rejects_a_start_above_the_dac_max(self):
        with pytest.raises(ProgramValidationError) as exc:
            self.piezo().check_stack_range(65536)
        assert exc.value.error == SeqError.STACK_OUT_OF_RANGE
        assert exc.value.detail == 0xFF

    def test_rejects_a_top_layer_past_the_dac_max(self):
        # start 65000 + 9 layers * 100 = 65900 > 65535
        with pytest.raises(ProgramValidationError) as exc:
            self.piezo(dz=100, n_layers=10).check_stack_range(65000)
        assert exc.value.error == SeqError.STACK_OUT_OF_RANGE
        assert exc.value.detail == 0

    def test_reports_the_offending_channel(self):
        with pytest.raises(ProgramValidationError) as exc:
            self.piezo(dz=0, n_layers=1, z_offsets=(0, 0, -5)).check_stack_range(1)
        assert exc.value.detail == 2

    def test_a_stepper_stack_has_the_full_int32_range(self):
        stepper = minimal_program(
            loop=minimal_program().loop.replace(
                stack_axis_type=StackAxisType.STEPPER, stack_axis_id=2, dz=-100000, n_layers=100
            )
        )
        stepper.check_stack_range(-1)
        stepper.check_stack_range(0)

    def test_a_stepper_stack_past_int32_is_rejected(self):
        stepper = minimal_program(
            loop=minimal_program().loop.replace(
                stack_axis_type=StackAxisType.STEPPER, stack_axis_id=2, dz=2**30, n_layers=10
            )
        )
        with pytest.raises(ProgramValidationError) as exc:
            stepper.check_stack_range(2**30)
        assert exc.value.error == SeqError.STACK_OUT_OF_RANGE


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def _parse_constexpr(text: str) -> dict:
    """constexpr uint8_t kFoo = 12;  ->  {"kFoo": 12}"""
    out = {}
    for name, value in re.findall(
        r"constexpr\s+uint(?:8|16|32)_t\s+(\w+)\s*=\s*(0[xX][0-9a-fA-F]+|\d+)\s*;", _strip_comments(text)
    ):
        out[name] = int(value, 0)
    return out


def _parse_enum(text: str, enum_name: str) -> dict:
    match = re.search(r"enum\s+class\s+" + enum_name + r"\s*:\s*uint8_t\s*\{(.*?)\}\s*;", text, re.S)
    assert match, f"could not find 'enum class {enum_name}' in the firmware header"
    out = {}
    next_value = 0
    for item in _strip_comments(match.group(1)).split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            name, value = item.split("=", 1)
            next_value = int(value.strip(), 0)
            name = name.strip()
        else:
            name = item
        out[name] = next_value
        next_value += 1
    return out


def _parse_static_assert_sizes(text: str) -> dict:
    return {
        name: int(size)
        for name, size in re.findall(r"static_assert\(\s*sizeof\((\w+)\)\s*==\s*(\d+)", _strip_comments(text))
    }


def _snake(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).upper()


class TestHeaderCrossCheck:
    """Parse the firmware headers and assert the Python mirror still matches them.

    Same idea as tests/control/test_firmware_sim_serial.py, applied to the sequencer's
    own headers.  If the firmware renumbers anything, this fails instead of the bench.
    """

    @pytest.fixture(scope="class")
    def seq_wire_h(self):
        return (FIRMWARE_SRC / "sequencer" / "seq_wire.h").read_text()

    @pytest.fixture(scope="class")
    def seq_types_h(self):
        return (FIRMWARE_SRC / "sequencer" / "seq_types.h").read_text()

    @pytest.fixture(scope="class")
    def seq_engine_h(self):
        return (FIRMWARE_SRC / "sequencer" / "seq_engine.h").read_text()

    @pytest.fixture(scope="class")
    def constants_protocol_h(self):
        return (FIRMWARE_SRC / "constants_protocol.h").read_text()

    def test_firmware_sources_are_present(self):
        assert (FIRMWARE_SRC / "sequencer" / "seq_wire.h").exists()

    def test_opcodes_match_seq_wire_h(self, seq_wire_h):
        k = _parse_constexpr(seq_wire_h)
        assert k["kOpSeqWrite"] == sp.OP_SEQ_WRITE
        assert k["kOpSeqCommit"] == sp.OP_SEQ_COMMIT
        assert k["kOpSeqRun"] == sp.OP_SEQ_RUN
        assert k["kOpSeqCancel"] == sp.OP_SEQ_CANCEL

    def test_opcodes_match_constants_protocol_h(self, constants_protocol_h):
        values = dict(
            (name, int(value))
            for name, value in re.findall(
                r"static\s+const\s+int\s+(\w+)\s*=\s*(-?\d+)\s*;", _strip_comments(constants_protocol_h)
            )
        )
        assert values["SEQ_WRITE"] == sp.OP_SEQ_WRITE
        assert values["SEQ_COMMIT"] == sp.OP_SEQ_COMMIT
        assert values["SEQ_RUN"] == sp.OP_SEQ_RUN
        assert values["SEQ_CANCEL"] == sp.OP_SEQ_CANCEL

    def test_wire_version_and_word_size(self, seq_wire_h):
        k = _parse_constexpr(seq_wire_h)
        assert k["kWireVersion"] == sp.WIRE_VERSION
        assert k["kWordBytes"] == sp.WORD_BYTES

    def test_status_byte_offsets(self, seq_wire_h):
        k = _parse_constexpr(seq_wire_h)
        assert k["kStatusByteState"] == sp.STATUS_BYTE_STATE
        assert k["kStatusByteDetail"] == sp.STATUS_BYTE_DETAIL
        assert k["kStatusByteFramesHi"] == sp.STATUS_BYTE_FRAMES_HI
        assert k["kStatusByteFramesLo"] == sp.STATUS_BYTE_FRAMES_LO

    def test_bad_program_detail_constants(self, seq_wire_h):
        k = _parse_constexpr(seq_wire_h)
        assert k["kBadProgramLength"] == sp.BAD_PROGRAM_LENGTH
        assert k["kBadProgramVersion"] == sp.BAD_PROGRAM_VERSION
        assert k["kBadProgramMismatch"] == sp.BAD_PROGRAM_MISMATCH
        assert k["kBadProgramCrc"] == sp.BAD_PROGRAM_CRC

    def test_allow_table_matches_seq_staging_cpp(self):
        """seq::wire::allowed_while_running() is the firmware's one choke point; the
        simulator mirrors it, and the host must never send anything else during a run."""
        import control.sequencer_sim as seq_sim
        from control._def import CMD_SET

        source = (FIRMWARE_SRC / "sequencer" / "seq_staging.cpp").read_text()
        body = re.search(r"bool allowed_while_running\(uint8_t opcode\) \{(.*?)\}", source, re.S).group(1)
        names = set(re.findall(r"opcode == (\w+)", body))
        assert names == {"HEARTBEAT", "SEQ_CANCEL", "TURN_OFF_ALL_PORTS", "RESET"}
        assert seq_sim.ALLOWED_WHILE_RUNNING == {getattr(CMD_SET, name) for name in names}

    def test_minimum_firmware_version(self, seq_wire_h):
        k = _parse_constexpr(seq_wire_h)
        assert (k["kFwMajor"], k["kFwMinor"]) == sp.MIN_FIRMWARE_VERSION

    def test_layout_offsets(self, seq_wire_h):
        k = _parse_constexpr(seq_wire_h)
        assert k["kLoopOffset"] == sp.LOOP_OFFSET
        assert k["kChannelsOffset"] == sp.CHANNELS_OFFSET

    def test_header_and_camera_struct_sizes(self, seq_wire_h):
        sizes = _parse_static_assert_sizes(seq_wire_h)
        assert sizes["WireHeader"] == sp.WIRE_HEADER_BYTES
        assert sizes["WireCamera"] == sp.WIRE_CAMERA_BYTES

    def test_loop_and_channel_struct_sizes(self, seq_types_h):
        sizes = _parse_static_assert_sizes(seq_types_h)
        assert sizes["SeqLoop"] == sp.SEQ_LOOP_BYTES
        assert sizes["SeqChannel"] == sp.SEQ_CHANNEL_BYTES

    def test_limits_match_seq_types_h(self, seq_types_h):
        k = _parse_constexpr(seq_types_h)
        assert k["kMaxChannels"] == sp.MAX_CHANNELS
        assert k["kMaxCameras"] == sp.MAX_CAMERAS
        assert k["kNone"] == sp.NONE_ID
        assert k["kMaxDurationUs"] == sp.MAX_DURATION_US
        assert k["kEdgePulseUs"] == sp.EDGE_PULSE_US

    def test_seq_error_numbering(self, seq_types_h):
        firmware = _parse_enum(seq_types_h, "SeqError")
        assert len(firmware) == len(
            SeqError
        ), f"firmware has {len(firmware)} SeqError values, python has {len(SeqError)}"
        for name, value in firmware.items():
            assert SeqError[_snake(name)].value == value, name

    def test_seq_state_numbering(self, seq_engine_h):
        firmware = _parse_enum(seq_engine_h, "SeqState")
        assert len(firmware) == len(SeqState)
        for name, value in firmware.items():
            assert SeqState[_snake(name)].value == value, name

    def test_small_enum_numbering(self, seq_types_h):
        for enum_name, python_enum in (
            ("StackAxisType", StackAxisType),
            ("Order", Order),
            ("TriggerMode", TriggerMode),
        ):
            firmware = _parse_enum(seq_types_h, enum_name)
            assert len(firmware) == len(python_enum)
            for name, value in firmware.items():
                assert python_enum[_snake(name)].value == value, f"{enum_name}.{name}"

    def test_staging_size_matches_the_header_expression(self, seq_wire_h, seq_types_h):
        wire = _parse_constexpr(seq_wire_h)
        types = _parse_constexpr(seq_types_h)
        sizes = dict(_parse_static_assert_sizes(seq_wire_h))
        sizes.update(_parse_static_assert_sizes(seq_types_h))
        expected = (
            wire["kChannelsOffset"]
            + types["kMaxChannels"] * sizes["SeqChannel"]
            + types["kMaxCameras"] * sizes["WireCamera"]
        )
        assert expected == sp.STAGING_BYTES
