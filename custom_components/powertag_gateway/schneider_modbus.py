import enum
import math
from datetime import datetime

from pymodbus.client import ModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder, BinaryPayloadBuilder
from pymodbus.pdu import ExceptionResponse

POWERTAG_LINK_SLAVE_ID = 255
SYNTHESIS_TABLE_SLAVE_ID_START = 247


class Phase(enum.Enum):
    A = 0
    B = 2
    C = 4


class LineVoltage(enum.Enum):
    A_B = 0
    B_C = 2
    C_A = 4
    A_N = 8
    B_N = 10
    C_N = 12

# Panelserver Status is different from gateway
class PanelserverStatus(enum.Enum):
    Nominal = 0b0000
    Degraded = 0b0001
    Outoforder = 0b0010
    # GENERAL_FAILURE = 0b1000
    # E2PROM_ERROR = 0b0010_0000_0000_1000
    # RAM_ERROR = 0b0100_0000_0000_1000
    # FLASH_ERROR = 0b1000_0000_0000_1000

class AlarmStatus:
    def __init__(self, bitmask: int):
        lower_mask = bitmask & 0b1111_1111

        self.has_alarm = lower_mask != 0
        self.alarm_voltage_loss = (lower_mask & 0b0000_0001) != 0
        self.alarm_current_overload = (lower_mask & 0b0000_0010) != 0
        # self.alarm_reserved = (lower_mask & 0b0000_0100) != 0
        self.alarm_overload_45_percent = (lower_mask & 0b0000_1000) != 0
        self.alarm_load_current_loss = (lower_mask & 0b0001_0000) != 0
        self.alarm_overvoltage = (lower_mask & 0b0010_0000) != 0
        self.alarm_undervoltage = (lower_mask & 0b0100_0000) != 0
        self.alarm_heattag_alarm = (lower_mask & 0b0001_0000_0000) != 0
        self.alarm_heattag_maintenance = (lower_mask & 0b0100_0000_0000) != 0
        self.alarm_heattag_replacement = (lower_mask & 0b1000_0000_0000) != 0


class DeviceUsage(enum.Enum):
    main_incomer = 1
    sub_head_of_group = 2
    heating = 3
    cooling = 4
    hvac = 5
    ventilation = 6
    lighting = 7
    office_equipment = 8
    cooking = 9
    food_refrigeration = 10
    elevators = 11
    computers = 12
    renewable_energy_production = 13
    genset = 14
    compressed_air = 15
    vapor = 16
    machine = 17
    process = 18
    water = 19
    other_sockets = 20
    other = 21
    INVALID = None


class PhaseSequence(enum.Enum):
    A = 1
    B = 2
    C = 3
    ABC = 4
    ACB = 5
    BCA = 6
    BAC = 7
    CAB = 8
    CBA = 9
    INVALID = None


class Position(enum.Enum):
    not_configured = 0
    top = 1
    bottom = 2
    not_applicable = 3
    INVALID = None


class ProductType(enum.Enum):
    A9MEM1520 = (41, "PowerTag M63 1P")
    A9MEM1521 = (42, "PowerTag M63 1P+N Top")
    A9MEM1522 = (43, "PowerTag M63 1P+N Bottom")
    A9MEM1540 = (44, "PowerTag M63 3P")
    A9MEM1541 = (45, "PowerTag M63 3P+N Top")
    A9MEM1542 = (46, "PowerTag M63 3P+N Bottom")
    A9MEM1560 = (81, "PowerTag F63 1P+N")
    A9MEM1561 = (82, "PowerTag P63 1P+N Top")
    A9MEM1562 = (83, "PowerTag P63 1P+N Bottom")
    A9MEM1563 = (84, "PowerTag P63 1P+N Bottom")
    A9MEM1570 = (85, "PowerTag F63 3P+N")
    A9MEM1571 = (86, "PowerTag P63 3P+N Top")
    A9MEM1572 = (87, "PowerTag P63 3P+N Bottom")
    LV434020 = (92, "PowerTag M250 3P")
    LV434021 = (93, "PowerTag M250 4P")
    LV434022 = (94, "PowerTag M630 3P")
    LV434023 = (95, "PowerTag M630 4P")
    A9MEM1543 = (96, "PowerTag M63 3P 230 V")
    A9XMC2D3 = (97, "PowerTag C 2DI 230 V")
    A9XMC1D3 = (98, "PowerTag C IO 230 V")
    A9MEM1564 = (101, "PowerTag F63 1P+N 110 V")
    A9MEM1573 = (102, "PowerTag F63 3P")
    A9MEM1574 = (103, "PowerTag F63 3P+N 110/230 V")
    A9MEM1590 = (104, "PowerTag R200")
    A9MEM1591 = (105, "PowerTag R600")
    A9MEM1592 = (106, "PowerTag R1000")
    A9MEM1593 = (107, "PowerTag R2000")
    A9MEM1580 = (121, "PowerTag F160")
    A9XMWRD = (170, "PowerTag Link display")
    SMT10020 = (171, "HeatTag sensor")


class SchneiderModbus:
    def __init__(self, host, port=502, timeout=5):
        self.client = ModbusTcpClient(host, port, timeout=timeout)
        self.client.connect()
        self.synthetic_slave_id = self.find_synthentic_table_slave_id()

    def find_synthentic_table_slave_id(self):
        for slave_id in range(SYNTHESIS_TABLE_SLAVE_ID_START, 1, -1):
            try:
                self.__read_int_16(0x0001, slave_id)
                return slave_id
            except ConnectionError:
                continue
        raise ConnectionError("Could not find synthetic slave ID")

    # Identification
    def hardware_version(self) -> str:
        """Gateway Hardware version
        valid for firmware version 001.008.007 and later.
        """
        return self.__read_string(0x0050, 6, POWERTAG_LINK_SLAVE_ID, 11)

    def serial_number(self) -> str:
        """[S/N]: PP YY WW [D [nnnn]]
        • PP: Plant
        • YY: Year in decimal notation [05...99]
        • WW: Week in decimal notation [1...53]
        • D: Day of the week in decimal notation [1...7]
        • nnnn: Sequence of numbers [0001...10.00-0–1]
        """
        return self.__read_string(0x0064, 6, POWERTAG_LINK_SLAVE_ID, 11)

    # legacy no longer supported
    #def hardware_version_legacy(self) -> str:
    #    """valid up to firmware version 001.008.007"""
    #    return self.__read_string(0x006A, 3, POWERTAG_LINK_SLAVE_ID, 6)

    # legacy no longer supported
    #def firmware_version_legacy(self) -> str:
    #    """valid up to firmware version 001.008.007"""
    #    return self.__read_string(0x006D, 3, POWERTAG_LINK_SLAVE_ID, 6)

    def firmware_version(self) -> str:
        """valid for firmware version 001.008.007 and later."""
        return self.__read_string(0x0078, 6, POWERTAG_LINK_SLAVE_ID, 11)

    # Status
    #OLD
    #def status(self) -> LinkStatus:
    #    """PowerTag Link gateway status and diagnostic register"""
    #    bitmap = self.__read_int_16(0x0070, POWERTAG_LINK_SLAVE_ID)
    #    return LinkStatus(bitmap)

    def status(self) -> PanelserverStatus:
        """PowerTag Link gateway status and diagnostic register"""
        bitmap = self.__read_int_16(0x009E, POWERTAG_LINK_SLAVE_ID)
        return PanelserverStatus(bitmap)

    # Date and Time

    def date_time(self) -> datetime | None:
        """Indicates the year, month, day, hour, minute and millisecond on the PowerTag Link gateway."""
        return self.__read_date_time(0x0073, POWERTAG_LINK_SLAVE_ID)

    # Current Metering Data

    def tag_current(self, power_tag_index: int, phase: Phase) -> float | None:
        """RMS current on phase"""
        return self.__read_float_32(0xBB7 + phase.value, power_tag_index)

    # Voltage Metering Data

    def tag_voltage(self, power_tag_index: int, line_voltage: LineVoltage) -> float | None:
        """RMS phase-to-phase voltage"""
        return self.__read_float_32(0xBCB + line_voltage.value, power_tag_index)

    # Power Metering Data

    def tag_power_active(self, power_tag_index: int, phase: Phase) -> float | None:
        """Active power on phase"""
        return self.__read_float_32(0xBED + phase.value, power_tag_index)

    def tag_power_active_total(self, power_tag_index: int) -> float | None:
        """Total active power"""
        return self.__read_float_32(0xBF3, power_tag_index)

    def tag_power_apparent_total(self, power_tag_index: int) -> float | None:
        """Total apparent power (arithmetric)"""
        return self.__read_float_32(0xC03, power_tag_index)

    # Power Factor Metering Data

    def tag_power_factor_total(self, power_tag_index: int) -> float | None:
        """Total power factor"""
        return self.__read_float_32(0xC0B, power_tag_index)

    # Energy Data – Legacy Zone

    def tag_energy_active_total(self, power_tag_index: int) -> int | None:
        """Total active energy delivered + received (not resettable)"""
        return self.__read_int_64(0xC83, power_tag_index)

    def tag_energy_active_partial(self, power_tag_index: int) -> int | None:
        """Partial active energy delivered + received (resettable)"""
        return self.__read_int_64(0xC83, power_tag_index)

    # Power Demand Data

    def tag_power_active_demand_total(self, power_tag_index: int) -> float | None:
        """Demand total active power"""
        return self.__read_float_32(0x0EB5, power_tag_index)

    def tag_power_active_power_demand_total_maximum(self, power_tag_index: int) -> float | None:
        """Maximum Demand total active power"""
        return self.__read_float_32(0x0EB9, power_tag_index)

    def tag_power_active_demand_total_maximum_timestamp(self, power_tag_index: int) -> datetime | None:
        """Maximum Demand total active power"""
        return self.__read_date_time(0x0EBB, power_tag_index)

    # Alarm

    def tag_is_alarm_valid(self, power_tag_index: int) -> bool | None:
        """Validity of the alarm bitmap"""
        return (self.__read_int_16(0xCE1, power_tag_index) & 0b1) != 0

    def tag_get_alarm(self, power_tag_index: int) -> AlarmStatus:
        """Alarms"""
        return AlarmStatus(self.__read_int_16(0xCE3, power_tag_index))

    def tag_current_at_voltage_loss(self, power_tag_index: int, phase: Phase) -> float | None:
        """RMS current on phase at voltage loss (last RMS current measured when voltage loss occurred)"""
        return self.__read_float_32(0xCE5 + phase.value, power_tag_index)

    # Load Operating Time

    def tag_load_operating_time(self, power_tag_index: int) -> int | None:
        """Load operating time counter."""
        return self.__read_int_32(0xCEB, power_tag_index)

    def tag_load_operating_time_active_power_threshold(self, power_tag_index: int) -> float | None:
        """Active power threshold for Load operating time counter. Counter starts above the threshold value."""
        return self.__read_float_32(0xCED, power_tag_index)

    def tag_load_operating_time_start(self, power_tag_index: int) -> datetime | None:
        """Date and time stamp of last Set or reset of Load operating time counter."""
        return self.__read_date_time(0xCEF, power_tag_index)

    # Configuration Registers

    def tag_name(self, power_tag_index: int) -> str | None:
        """User application name of the wireless device. The user can enter maximum 20 characters."""
        return self.__read_string(0x7918, 10, power_tag_index, 20)

    def tag_circuit(self, power_tag_index: int) -> str:
        """Circuit identifier of the wireless device. The user can enter maximum five characters."""
        return self.__read_string(0x7922, 3, power_tag_index, 5)

    def tag_usage(self, power_tag_index: int) -> DeviceUsage:
        """Indicates the usage of the wireless device."""
        return DeviceUsage(self.__read_int_16(0x7925, power_tag_index))

    def tag_phase_sequence(self, power_tag_index: int) -> PhaseSequence:
        """Phase sequence."""
        return PhaseSequence(self.__read_int_16(0x7926, power_tag_index))

    def tag_position(self, power_tag_index: int) -> Position:
        """Mounting position"""
        return Position(self.__read_int_16(0x7927, power_tag_index))

    def tag_circuit_diagnostic(self, power_tag_index: int) -> Position:
        """Circuit diagnostics"""
        return Position(self.__read_int_16(0x7928, power_tag_index))

    def tag_rated_current(self, power_tag_index: int) -> int | None:
        """Rated current of the protective device to the wireless device"""
        return self.__read_int_16(0x7929, power_tag_index)

    def tag_rated_voltage(self, power_tag_index: int) -> float | None:
        """Rated voltage"""
        return self.__read_float_32(0x792B, power_tag_index)

    def tag_reset_peak_demands(self, power_tag_index: int):
        """Reset All Peak Demands"""
        self.__write_int_16(0x792E, power_tag_index, 1)

    def tag_power_supply_type(self, power_tag_index: int):
        """Power supply type"""
        return Position(self.__read_int_16(0x792F, power_tag_index))

    # Device identification

    def tag_product_type(self, power_tag_index: int) -> ProductType | None:
        """Wireless device code type"""
        code = self.__read_int_16(0x7930, power_tag_index)
        product_type = [p for p in ProductType if p.value[0] == code]
        return product_type[0] if product_type else None

    def tag_slave_address(self, power_tag_index: int) -> int | None:
        """Virtual Modbus server address"""
        return self.__read_int_16(0x7931, power_tag_index)

    def tag_rf_id(self, power_tag_index: int) -> int | None:
        """Wireless device Radio Frequency Identifier"""
        return self.__read_int_64(0x7932, power_tag_index)

    def tag_product_identifier(self, power_tag_index: int) -> int | None:
        """Wireless device identifier"""
        return self.__read_int_16(0x7937, power_tag_index)

    def tag_vendor_name(self, power_tag_index: int) -> str | None:
        """Vendor name"""
        return self.__read_string(0x7944, 16, power_tag_index, 32)

    def tag_product_code(self, power_tag_index: int) -> str | None:
        """Wireless device commercial reference"""
        return self.__read_string(0x7954, 16, power_tag_index, 32)

    def tag_firmware_revision(self, power_tag_index: int) -> str | None:
        """Firmware revision"""
        return self.__read_string(0x7964, 6, power_tag_index, 12)

    def tag_hardware_revision(self, power_tag_index: int) -> str | None:
        """Hardware revision"""
        return self.__read_string(0x796A, 6, power_tag_index, 12)

    def tag_serial_number(self, power_tag_index: int) -> str | None:
        """Serial number"""
        return self.__read_string(0x7970, 10, power_tag_index, 20)

    def tag_product_range(self, power_tag_index: int) -> str | None:
        """Product range"""
        return self.__read_string(0x797A, 8, power_tag_index, 16)

    def tag_product_model(self, power_tag_index: int) -> str | None:
        """Product model"""
        return self.__read_string(0x7982, 8, power_tag_index, 16)

    def tag_product_family(self, power_tag_index: int) -> str | None:
        """Product family"""
        return self.__read_string(0x798A, 8, power_tag_index, 16)

    # Diagnostic Data Registers

    def tag_radio_communication_valid(self, power_tag_index: int) -> bool:
        """Validity of the RF communication between PowerTag system and PowerTag Link gateway status."""
        return self.__read_int_16(0x79A8, power_tag_index) != 0

    def tag_wireless_communication_valid(self, power_tag_index: int) -> bool:
        """Communication status between PowerTag Link gateway and wireless devices."""
        return self.__read_int_16(0x79A9, power_tag_index) != 0

    def tag_radio_per_tag(self, power_tag_index: int) -> float | None:
        """Packet Error Rate (PER) of the device, received by PowerTag Link gateway"""
        return self.__read_float_32(0x79B4, power_tag_index)

    def tag_radio_rssi_inside_tag(self, power_tag_index: int) -> float | None:
        """RSSI of the device, received by PowerTag Link gateway"""
        return self.__read_float_32(0x79B6, power_tag_index)

    def tag_radio_lqi_tag(self, power_tag_index: int) -> int | None:
        """Link Quality Indicator (LQI) of the device, received by PowerTag Link gateway"""
        return self.__read_int_16(0x79B8, power_tag_index)

    def tag_radio_per_gateway(self, power_tag_index: int) -> float | None:
        """PER of gateway, calculated inside the PowerTag Link gateway"""
        return self.__read_float_32(0x79AF, power_tag_index)

    def tag_radio_rssi_inside_gateway(self, power_tag_index: int) -> float | None:
        """Radio Signal Strength Indicator (RSSI) of gateway, calculated inside the PowerTag Link gateway"""
        return self.__read_float_32(0x79B1, power_tag_index)

    def tag_radio_lqi_gateway(self, power_tag_index: int) -> float | None:
        """LQI of gateway, calculated insider the PowerTag Link gateway"""
        return self.__read_int_16(0x79B3, power_tag_index)

    def tag_radio_per_maximum(self, power_tag_index: int) -> float | None:
        """PER–Maximum value between device and gateway"""
        return self.__read_float_32(0x79B4, power_tag_index)

    def tag_radio_rssi_minimum(self, power_tag_index: int) -> float | None:
        """RSSI–Minimal value between device and gateway"""
        return self.__read_float_32(0x79B6, power_tag_index)

    def tag_radio_lqi_minimum(self, power_tag_index: int) -> float | None:
        """LQI–Minimal value between device and gateway"""
        return self.__read_int_16(0x79B8, power_tag_index)

    # Identification and Status Register

    def product_id(self) -> int | None:
        """Product ID of the synthesis table"""
        return self.__read_int_16(0x0001, self.synthetic_slave_id)

    def manufacturer(self) -> str | None:
        """Product ID of the synthesis table"""
        return self.__read_string(0x0002, 16, self.synthetic_slave_id, 32)

    def product_code(self) -> str | None:
        """Commercial reference of the gateway"""
        return self.__read_string(0x0012, 16, self.synthetic_slave_id, 32)

    def product_range(self) -> str | None:
        """Product range of the gateway"""
        return self.__read_string(0x0022, 8, self.synthetic_slave_id, 16)

    def product_model(self) -> str | None:
        """Product model"""
        return self.__read_string(0x002A, 8, self.synthetic_slave_id, 16)

    def name(self) -> str | None:
        """Asset name"""
        return self.__read_string(0x0032, 10, self.synthetic_slave_id, 20)

    def product_vendor_url(self) -> str | None:
        """Vendor URL"""
        return self.__read_string(0x003C, 17, self.synthetic_slave_id, 34)

    # Wireless Configured Devices – 100 Devices

    def modbus_address_of_node(self, node_index: int) -> int | None:
        return self.__read_int_16(0x012C + node_index - 1, self.synthetic_slave_id)

    # Helper functions

    def __write(self, address: int, registers, unit: int):
        response = self.client.write_registers(address, registers, unit=unit)
        print(response)

    def __read(self, address: int, count: int, unit: int):
        response = self.client.read_holding_registers(address, count, slave=unit)
        if isinstance(response, ExceptionResponse):
            raise ConnectionError(str(response))
        return response.registers

    @staticmethod
    def decoder(registers):
        return BinaryPayloadDecoder.fromRegisters(
            registers, byteorder=Endian.Big, wordorder=Endian.Big
        )

    def __read_string(self, address: int, count: int, unit: int, string_length: int) -> str | None:
        registers = self.__read(address, count, unit)
        ascii_bytes = self.decoder(registers).decode_string(string_length)

        if all(c == '\x00' for c in ascii_bytes):
            return None

        filtered_ascii_bytes = bytes(filter(lambda b: b != 0, list(ascii_bytes)))
        return bytes.decode(filtered_ascii_bytes)

    def __write_string(self, address: int, unit: int, string: str):
        builder = BinaryPayloadBuilder(byteorder=Endian.Big, wordorder=Endian.Big)
        builder.add_string(string.ljust(20, '\x00'))
        self.__write(address, builder.to_registers(), unit)

    def __read_float_32(self, address: int, unit: int) -> float | None:
        assert (1 <= unit <= 247) or (unit == 255)
        result = self.decoder(self.__read(address, 2, unit)).decode_32bit_float()
        return result if not math.isnan(result) else None

    def __read_int_16(self, address: int, unit: int) -> int | None:
        assert (1 <= unit <= 247) or (unit == 255)
        result = self.decoder(self.__read(address, 1, unit)).decode_16bit_uint()
        return result if result != 0xFFFF else None

    def __write_int_16(self, address: int, unit: int, value: int):
        assert (1 <= unit <= 247) or (unit == 255)
        builder = BinaryPayloadBuilder(byteorder=Endian.Big, wordorder=Endian.Big)
        builder.add_16bit_uint(value)
        self.__write(address, builder.to_registers(), unit)

    def __read_int_32(self, address: int, unit: int) -> int | None:
        assert (1 <= unit <= 247) or (unit == 255)
        result = self.decoder(self.__read(address, 2, unit)).decode_32bit_uint()
        return result if result != 0x8000_0000 else None

    def __read_int_64(self, address: int, unit: int) -> int | None:
        assert (1 <= unit <= 247) or (unit == 255)
        result = self.decoder(self.__read(address, 4, unit)).decode_64bit_uint()
        return result if result != 0x8000_0000_0000_0000 else None

    def __read_date_time(self, address: int, unit) -> datetime | None:
        d = self.decoder(self.__read(address, 4, unit))

        year_raw = d.decode_16bit_uint()
        year = (year_raw & 0b0111_1111) + 2000

        day_month = d.decode_16bit_uint()
        day = day_month & 0b0001_1111
        month = (day_month >> 8) & 0b0000_1111

        minute_hour = d.decode_16bit_uint()
        minute = minute_hour & 0b0011_1111
        hour = (minute_hour >> 8) & 0b0001_1111

        second_millisecond = d.decode_16bit_uint()
        second = math.floor(second_millisecond / 1000)
        millisecond = second_millisecond - second * 1000

        if year_raw == 0xFFFF and day_month == 0xFFFF and minute_hour == 0xFFFF and second_millisecond == 0xFFFF:
            return None

        return datetime(year, month, day, hour, minute, second, millisecond)
