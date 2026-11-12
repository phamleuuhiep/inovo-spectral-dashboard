import struct
from enum import Enum


class MessageType(Enum):
    STOP = 0x04
    GET_DEVICE_ID = 0x08
    # Xác nhận bằng Serial Port Monitor khi chuyển Auto -> Manual rồi bấm "Set" trong Flameeye (25/09/2026):
    #   gửi  cc 01 0a 00 00 0a 00 e1 0d 0a           -> SET mode MANUAL, thiết bị ack "0a 00"
    #   gửi  cc 01 09 00 00 0b e1 0d 0a              -> GET mode, thiết bị trả "0b 00" (MANUAL)
    #   gửi  cc 01 0d 00 00 0c 40 42 0f 00 77 0d 0a  -> SET 1000 ms, thiết bị ack "0c 00"
    #   gửi  cc 01 09 00 00 0d e3 0d 0a              -> GET, thiết bị trả "0d 40 42 0f 00"
    SET_EXPOSURE_MODE = 0x0A
    GET_EXPOSURE_MODE = 0x0B
    SET_EXPOSURE_TIME = 0x0C
    GET_EXPOSURE_TIME = 0x0D
    GET_RANGE = 0x0F
    GET_DATA = 0x33


class ExposureMode(Enum):
    MANUAL = 0x00
    AUTOMATIC = 0x01


class ExposureStatus(Enum):
    NORMAL = 0x00
    OVER = 0x01
    UNDER = 0x02


def decode_spectrum(encoded_spectrum, encoded_exponent, exposure_time, sn, ex_info):
    exposure_time_bytes = struct.pack("<f", exposure_time)
    exposure_time = int.from_bytes(exposure_time_bytes, "little")

    common = int.from_bytes(exposure_time_bytes, "big") ^ ex_info >> 16
    key_a = (common ^ (exposure_time ^ sn) >> 16 ^ sn ^ ex_info) & 0xFFFF
    key_b = (common >> 16 ^ exposure_time ^ sn) & 0xFFFF

    exponent = struct.unpack(">H", struct.pack("<H", encoded_exponent))[0] ^ 8848
    scale = pow(10, exponent)

    midpoint = len(encoded_spectrum) // 2

    return [
        (item ^ (key_a if index < midpoint else key_b)) / scale
        for index, item in enumerate(encoded_spectrum)
    ]


def calculate_checksum(message):
    return sum(message) & 0xFF


def build_message(message_type, data):
    message = b"\xCC\x01"
    message += int.to_bytes(9 + len(data), 3, "little")
    message += int.to_bytes(message_type.value, 1, "little")
    message += data
    message += int.to_bytes(calculate_checksum(message), 1, "little")
    message += b"\x0D\x0A"

    return message


def build_set_exposure_mode(mode):
    return build_message(MessageType.SET_EXPOSURE_MODE, bytes([mode.value]))


def build_set_exposure_time(exposure_ms):
    # exposure_time là uint32 micro giây, little-endian (cùng đơn vị với GET_DATA)
    return build_message(
        MessageType.SET_EXPOSURE_TIME, struct.pack("<I", int(round(exposure_ms * 1000)))
    )


def parse_message(message_type, data):
    message = {"message_type": message_type}

    match message_type:
        case MessageType.GET_DEVICE_ID:
            message["device_id"] = data.decode("ascii")

        case MessageType.GET_EXPOSURE_MODE:
            message["exposure_mode"] = ExposureMode(data[0])

        case MessageType.SET_EXPOSURE_MODE | MessageType.SET_EXPOSURE_TIME:
            message["ack_status"] = data[0] if data else None

        case MessageType.GET_EXPOSURE_TIME:
            message["exposure_time"] = struct.unpack("<I", data)[0] / 1000

        case MessageType.GET_RANGE:
            message["start_wavelength"], message["end_wavelength"] = struct.unpack(
                "<HH", data
            )

        case MessageType.GET_DATA:
            (
                exposure_status_code,
                exposure_time_microseconds,
                encoded_exponent,
                sn,
                ex_info,
            ) = struct.unpack_from("<BIHIQ", data)

            encoded_spectrum = [item[0] for item in struct.iter_unpack("<H", data[19:])]

            message["exposure_status"] = ExposureStatus(exposure_status_code)
            message["exposure_time"] = exposure_time_microseconds / 1000
            # Giá trị ADC thô, chưa qua decode_spectrum (scale factor của decode_spectrum
            # không đáng tin cậy trên thiết bị này và làm giá trị co về gần 0)
            message["raw_spectrum"] = encoded_spectrum
            message["spectrum"] = decode_spectrum(
                encoded_spectrum,
                encoded_exponent,
                message["exposure_time"],
                sn,
                ex_info,
            )

    return message


def parse_messages(data):
    messages = []

    while len(data) >= 5:
        if not data.startswith(b"\xCC\x81"):
            raise ValueError("Invalid start bytes")

        length = int.from_bytes(data[2:5], "little")

        if len(data) < length:
            break

        if calculate_checksum(data[: length - 3]) != data[length - 3]:
            raise ValueError("Invalid checksum")

        if data[length - 2 : length] != b"\x0D\x0A":
            raise ValueError("Invalid end bytes")

        messages.append(parse_message(MessageType(data[5]), data[6 : 6 + length - 9]))
        data = data[length:]

    return (data, messages)