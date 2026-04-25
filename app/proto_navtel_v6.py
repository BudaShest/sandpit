"""Navtelecom v6.x protocol parser."""
import re
import struct
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone


class NavtelParseError(Exception):
    """Navtelecom protocol parsing error."""
    pass


def _extract_ntc_coordinates(text: str) -> Dict[str, Any]:
    """Best-effort extraction of coordinates/speed from NTC text payload."""
    result: Dict[str, Any] = {}
    number_matches = list(re.finditer(r"[-+]?\d+(?:\.\d+)?", text))
    values = []
    for m in number_matches:
        token = m.group(0)
        if "." not in token:
            continue
        try:
            values.append(float(token))
        except ValueError:
            continue

    # Look for first plausible lat/lon pair in decimal degrees.
    for i in range(len(values) - 1):
        a = values[i]
        b = values[i + 1]
        if -90 <= a <= 90 and -180 <= b <= 180:
            result["lat"] = a
            result["lon"] = b
            if i + 2 < len(values) and 0 <= values[i + 2] <= 400:
                result["speed"] = values[i + 2]
            break

    # Optional: timestamp if Unix epoch appears in text.
    ts_match = re.search(r"\b(1[6-9]\d{8}|2\d{9})\b", text)
    if ts_match:
        try:
            ts = int(ts_match.group(1))
            if 946684800 <= ts <= 4102444800:
                result["device_time"] = datetime.fromtimestamp(ts, tz=timezone.utc)
        except ValueError:
            pass

    return result


def _try_parse_ntc_greeting(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse NTC greeting/login packet (e.g. '@NTC...S:869132076048835')."""
    if not data:
        return None
    try:
        text = data.decode("ascii", errors="ignore")
    except Exception:
        return None

    if "@NTC" not in text:
        return None

    imei_match = re.search(r"S:(\d{15})", text)
    device_id = imei_match.group(1) if imei_match else "unknown_ntc_device"

    parsed = {
        "device_id": device_id,
        "device_time": datetime.now(timezone.utc),
        "data_type": 0x00,
        "protocol_hint": "ntc_greeting",
        "ntc_raw_text": text,
    }
    parsed.update(_extract_ntc_coordinates(text))
    if "lat" in parsed and "lon" in parsed:
        parsed["protocol_hint"] = "ntc_text_payload"
    return parsed


def _try_parse_flex_emulator_binary(data: bytes) -> Optional[Dict[str, Any]]:
    """
    Heuristic parser for FLEX emulator binary packets observed in logs.

    Packet shape (observed):
    - 0x7E
    - frame marker/type byte (often 0x54 == 'T')
    - ... payload ...
    - 0x7E
    """
    if len(data) < 40 or data[0] != 0x7E:
        return None
    if data[1] not in (0x54, ord("T"), ord("A")):
        return None

    try:
        primary_ts = struct.unpack("<I", data[12:16])[0]
        secondary_ts = struct.unpack("<I", data[20:24])[0]
        lat_raw = struct.unpack("<i", data[24:28])[0]
        lon_raw = struct.unpack("<i", data[28:32])[0]
        altitude_raw = struct.unpack("<I", data[32:36])[0]
        speed_raw = struct.unpack("<f", data[36:40])[0]
    except struct.error:
        return None

    # Guardrails to avoid false-positive parsing for arbitrary binary data.
    if not (946684800 <= primary_ts <= 4102444800):
        return None
    if not (primary_ts - 300 <= secondary_ts <= primary_ts + 300):
        return None
    if not (0.0 <= speed_raw <= 400.0):
        return None

    # For observed emulator packets coordinates are scaled by 600000.
    lat = lat_raw / 600000.0
    lon = lon_raw / 600000.0
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    return {
        "device_id": "flex_emulator",
        "device_time": datetime.fromtimestamp(primary_ts, tz=timezone.utc),
        "data_type": 0x01,  # treat as GPS-like frame
        "lat": lat,
        "lon": lon,
        "speed": float(speed_raw),
        "course": float(struct.unpack("<H", data[18:20])[0]),
        "altitude": altitude_raw / 10.0,
        "protocol_hint": "flex_emulator_binary",
        "raw_data": data.hex(),
    }


def _try_parse_ascii_navtel(data: bytes) -> Optional[Dict[str, Any]]:
    """
    Текстовые кадры (scripts/test_client.py): ~AIMEI,unix_ts,lat,lon,speed,course,sats,hdop~
    и упрощённо ~T…~, ~E…~ — без бинарной длины/CRC.
    """
    if len(data) < 7 or data[0] != 0x7E or data[-1] != 0x7E:
        return None
    try:
        inner = data[1:-1].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not inner:
        return None
    kind = inner[0]
    if kind not in ("A", "T", "E"):
        return None
    if len(inner) < 2 or inner[1] not in "0123456789":
        return None
    if kind == "A":
        parts = inner[1:].split(",")
        if len(parts) < 8:
            return None
        imei, ts, lat_s, lon_s, speed_s, course_s, sats_s, _hdop = parts[:8]
        try:
            return {
                "device_id": imei,
                "device_time": datetime.fromtimestamp(int(ts), tz=timezone.utc),
                "data_type": 0x01,
                "lat": float(lat_s),
                "lon": float(lon_s),
                "speed": float(speed_s),
                "course": float(course_s),
                "satellites": int(float(sats_s)),
                "ignition": None,
            }
        except (ValueError, OSError):
            return None
    if kind == "T":
        parts = inner[1:].split(",")
        if len(parts) < 2:
            return None
        return {
            "device_id": parts[0],
            "device_time": datetime.now(timezone.utc),
            "data_type": 0x04,
            "can_frames": [],
        }
    if kind == "E":
        parts = inner[1:].split(",")
        if len(parts) < 2:
            return None
        return {
            "device_id": parts[0],
            "device_time": datetime.now(timezone.utc),
            "data_type": 0x03,
        }
    return None


def calculate_crc16(data: bytes) -> int:
    """Calculate CRC16 for Navtelecom protocol."""
    crc = 0xFFFF
    
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    
    return crc


def try_parse_frame(data: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse Navtelecom v6.x frame.
    
    Frame format (simplified):
    - Start byte: 0x7E
    - Length: 2 bytes (little endian)
    - Data: variable length
    - CRC: 2 bytes (little endian)
    - End byte: 0x7E
    """
    ntc_greeting = _try_parse_ntc_greeting(data)
    if ntc_greeting is not None:
        return ntc_greeting

    ascii_parsed = _try_parse_ascii_navtel(data)
    if ascii_parsed is not None:
        return ascii_parsed

    emulator_parsed = _try_parse_flex_emulator_binary(data)
    if emulator_parsed is not None:
        return emulator_parsed

    if len(data) < 6:  # Minimum frame size
        return None
    
    # For non-0x7E protocols just return None (no hard protocol error).
    # This allows upstream code to persist raw payloads and inspect them.
    if data[0] != 0x7E or data[-1] != 0x7E:
        return None
    
    # Extract length
    try:
        length = struct.unpack('<H', data[1:3])[0]
    except struct.error:
        raise NavtelParseError("Invalid length field")
    
    # Check frame size
    if len(data) < length + 6:  # length + start + end + crc
        return None  # Incomplete frame
    
    # Extract data and CRC
    frame_data = data[3:3+length]
    crc_received = struct.unpack('<H', data[3+length:3+length+2])[0]
    
    # Verify CRC
    crc_calculated = calculate_crc16(frame_data)
    if crc_received != crc_calculated:
        raise NavtelParseError(f"CRC mismatch: received {crc_received:04X}, calculated {crc_calculated:04X}")
    
    # Parse frame data
    return parse_frame_data(frame_data)


def parse_frame_data(data: bytes) -> Dict[str, Any]:
    """Parse frame data according to Navtelecom v6.x protocol."""
    if len(data) < 4:
        raise NavtelParseError("Frame data too short")
    
    # Extract device ID (IMEI) - first 8 bytes
    device_id = data[:8].hex()
    
    # Extract timestamp (4 bytes, Unix timestamp)
    timestamp = struct.unpack('<I', data[8:12])[0]
    device_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    
    # Parse data type
    data_type = data[12] if len(data) > 12 else 0
    
    result = {
        "device_id": device_id,
        "device_time": device_time,
        "data_type": data_type,
        "raw_data": data.hex()
    }
    
    # Parse based on data type
    if data_type == 0x01:  # GPS data
        result.update(parse_gps_data(data[13:]))
    elif data_type == 0x02:  # CAN data (legacy)
        result.update(parse_can_data(data[13:]))
    elif data_type == 0x03:  # Event data
        result.update(parse_event_data(data[13:]))
    elif data_type == 0x04:  # Raw CAN data (new)
        result.update(parse_raw_can_data(data[13:]))
    elif data_type == 0x05:  # Extended data
        result.update(parse_extended_data(data[13:]))
    else:
        result["unknown_data"] = data[13:].hex()
    
    return result


def parse_gps_data(data: bytes) -> Dict[str, Any]:
    """Parse GPS data from frame."""
    if len(data) < 20:
        raise NavtelParseError("GPS data too short")
    
    # Parse coordinates (4 bytes each, signed integer, scale 1e7)
    lat_raw = struct.unpack('<i', data[0:4])[0]
    lon_raw = struct.unpack('<i', data[4:8])[0]
    
    latitude = lat_raw / 1e7
    longitude = lon_raw / 1e7
    
    # Parse speed (2 bytes, km/h * 10)
    speed_raw = struct.unpack('<H', data[8:10])[0]
    speed = speed_raw / 10.0
    
    # Parse course (2 bytes, degrees * 10)
    course_raw = struct.unpack('<H', data[10:12])[0]
    course = course_raw / 10.0
    
    # Parse altitude (2 bytes, meters)
    altitude = struct.unpack('<H', data[12:14])[0]
    
    # Parse satellites count
    satellites = data[14] if len(data) > 14 else 0
    
    # Parse ignition status
    ignition = bool(data[15] & 0x01) if len(data) > 15 else None
    
    return {
        "lat": latitude,
        "lon": longitude,
        "speed": speed,
        "course": course,
        "altitude": altitude,
        "satellites": satellites,
        "ignition": ignition
    }


def parse_can_data(data: bytes) -> Dict[str, Any]:
    """Parse CAN data from frame."""
    if len(data) < 4:
        raise NavtelParseError("CAN data too short")
    
    # Parse CAN ID (4 bytes)
    can_id = struct.unpack('<I', data[0:4])[0]
    
    # Parse CAN data (remaining bytes)
    can_data = data[4:].hex()
    
    return {
        "can_id": can_id,
        "can_data": can_data
    }


def parse_event_data(data: bytes) -> Dict[str, Any]:
    """Parse event data from frame."""
    if len(data) < 2:
        raise NavtelParseError("Event data too short")
    
    # Parse event code (2 bytes)
    event_code = struct.unpack('<H', data[0:2])[0]
    
    # Parse event data (remaining bytes)
    event_data = data[2:].hex()
    
    return {
        "event_code": event_code,
        "event_data": event_data
    }


def parse_raw_can_data(data: bytes) -> Dict[str, Any]:
    """Parse raw CAN data from frame."""
    if len(data) < 8:
        raise NavtelParseError("Raw CAN data too short")
    
    can_frames = []
    offset = 0
    
    while offset < len(data):
        if offset + 8 > len(data):
            break
        
        # Parse CAN frame header: [timestamp(4)][can_id(4)][dlc(1)][is_extended(1)]
        timestamp = struct.unpack('<I', data[offset:offset+4])[0]
        can_id = struct.unpack('<I', data[offset+4:offset+8])[0]
        dlc = data[offset+8] if offset+8 < len(data) else 0
        is_extended = bool(data[offset+9]) if offset+9 < len(data) else False
        
        offset += 10
        
        # Parse CAN payload
        if offset + dlc > len(data):
            break
        
        payload = data[offset:offset+dlc]
        offset += dlc
        
        can_frames.append({
            "timestamp": timestamp,
            "can_id": can_id,
            "dlc": dlc,
            "is_extended": is_extended,
            "payload": payload.hex()
        })
    
    return {
        "can_frames": can_frames,
        "frame_count": len(can_frames)
    }


def parse_extended_data(data: bytes) -> Dict[str, Any]:
    """Parse extended data from frame."""
    if len(data) < 4:
        raise NavtelParseError("Extended data too short")
    
    # Parse extended data type (2 bytes)
    ext_type = struct.unpack('<H', data[0:2])[0]
    
    # Parse data length (2 bytes)
    data_length = struct.unpack('<H', data[2:4])[0]
    
    # Parse extended data
    ext_data = data[4:4+data_length].hex() if data_length > 0 else ""
    
    return {
        "extended_type": ext_type,
        "extended_data": ext_data,
        "data_length": data_length
    }


def generate_ack_response(device_id: str, data_type: int, status: int = 0x00) -> bytes:
    """Generate ACK response for Navtelecom protocol."""
    # ACK response format: [ACK_FLAG][STATUS][DEVICE_ID_HASH]
    ack_data = bytearray()
    ack_data.append(0x01)  # ACK flag
    ack_data.append(status)  # Status (0x00 = OK, 0x01 = CRC_ERROR, 0x02 = FORMAT_ERROR)
    
    # Add device ID hash for correlation
    device_hash = hash(device_id) & 0xFFFF
    ack_data.extend(struct.pack('<H', device_hash))
    
    crc = calculate_crc16(ack_data)
    
    # Build response frame
    response = bytearray()
    response.append(0x7E)  # Start marker
    response.extend(struct.pack('<H', len(ack_data)))  # Length
    response.extend(ack_data)  # Data
    response.extend(struct.pack('<H', crc))  # CRC
    response.append(0x7E)  # End marker
    
    return bytes(response)


def generate_nack_response(device_id: str, error_code: int) -> bytes:
    """Generate NACK response for Navtelecom protocol."""
    # NACK response format: [NACK_FLAG][ERROR_CODE][DEVICE_ID_HASH]
    nack_data = bytearray()
    nack_data.append(0x02)  # NACK flag
    nack_data.append(error_code)  # Error code
    
    # Add device ID hash for correlation
    device_hash = hash(device_id) & 0xFFFF
    nack_data.extend(struct.pack('<H', device_hash))
    
    crc = calculate_crc16(nack_data)
    
    # Build response frame
    response = bytearray()
    response.append(0x7E)  # Start marker
    response.extend(struct.pack('<H', len(nack_data)))  # Length
    response.extend(nack_data)  # Data
    response.extend(struct.pack('<H', crc))  # CRC
    response.append(0x7E)  # End marker
    
    return bytes(response)


def summarize_binary_frame(data: bytes) -> Optional[Dict[str, Any]]:
    """
    Build human-readable preview for opaque binary frames.

    Used only for logging/diagnostics when full protocol parsing fails.
    """
    if not data:
        return None
    if data[0] != 0x7E:
        return None

    def _hex_preview(buf: bytes, limit: int = 120) -> str:
        value = buf.hex()
        if len(value) <= limit:
            return value
        return f"{value[:limit]}..."

    def _ascii_preview(buf: bytes, limit: int = 64) -> str:
        rendered = "".join(chr(b) if 32 <= b <= 126 else "." for b in buf)
        if len(rendered) <= limit:
            return rendered
        return f"{rendered[:limit]}..."

    has_trailing_7e = data[-1] == 0x7E
    declared_length = None
    expected_total_bytes = None
    length_matches = None
    if len(data) >= 3:
        declared_length = struct.unpack("<H", data[1:3])[0]
        expected_total_bytes = declared_length + 6  # 0x7E + len(2) + payload + crc(2) + 0x7E
        length_matches = len(data) == expected_total_bytes

    # Heuristic: collect likely Unix timestamps from little-endian uint32 windows.
    timestamp_candidates: List[Dict[str, Any]] = []
    for offset in range(0, len(data) - 3):
        value = struct.unpack("<I", data[offset:offset + 4])[0]
        if 946684800 <= value <= 4102444800:  # 2000-01-01 .. 2100-01-01
            timestamp_candidates.append({
                "offset": offset,
                "unix": value,
                "utc": datetime.fromtimestamp(value, tz=timezone.utc).isoformat(),
            })
            if len(timestamp_candidates) >= 5:
                break

    quick_decode: Dict[str, Any] = {}
    if len(data) >= 2:
        frame_type_byte = data[1]
        quick_decode["frame_type_byte"] = frame_type_byte
        if 32 <= frame_type_byte <= 126:
            quick_decode["frame_type_char"] = chr(frame_type_byte)

    # Heuristic decode for frames that look like emulator packets:
    # ~ [type] [..] [unix ts] [speed] [unix ts] [lat] [lon] [..] [crc] ~
    if len(data) >= 34:
        primary_ts = struct.unpack("<I", data[12:16])[0]
        secondary_ts = struct.unpack("<I", data[20:24])[0]
        speed_raw = struct.unpack("<H", data[18:20])[0]
        lat_raw = struct.unpack("<i", data[24:28])[0]
        lon_raw = struct.unpack("<i", data[28:32])[0]

        quick_decode["primary_timestamp_unix"] = primary_ts
        quick_decode["primary_timestamp_utc"] = datetime.fromtimestamp(
            primary_ts, tz=timezone.utc
        ).isoformat()
        quick_decode["secondary_timestamp_unix"] = secondary_ts
        quick_decode["secondary_timestamp_utc"] = datetime.fromtimestamp(
            secondary_ts, tz=timezone.utc
        ).isoformat()
        quick_decode["speed_raw"] = speed_raw
        quick_decode["lat_raw"] = lat_raw
        quick_decode["lon_raw"] = lon_raw

        coord_candidates: List[Dict[str, Any]] = []
        for divisor in (600_000, 1_000_000, 10_000_000):
            lat = lat_raw / divisor
            lon = lon_raw / divisor
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                coord_candidates.append({
                    "scale_divisor": divisor,
                    "lat": round(lat, 7),
                    "lon": round(lon, 7),
                })
        if coord_candidates:
            quick_decode["coordinate_candidates"] = coord_candidates

    summary = {
        "frame_kind": "binary_7e",
        "has_trailing_7e": has_trailing_7e,
        "total_bytes": len(data),
        "declared_length": declared_length,
        "expected_total_bytes": expected_total_bytes,
        "length_matches": length_matches,
        "frame_hex_preview": _hex_preview(data),
        "ascii_preview": _ascii_preview(data),
        "timestamp_candidates": timestamp_candidates,
    }
    if quick_decode:
        summary["quick_decode"] = quick_decode
    return summary