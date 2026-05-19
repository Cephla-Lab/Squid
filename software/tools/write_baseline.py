"""把正常驱动器的参数基线写入设备 EEPROM。

独立脚本，只依赖 pyserial（pip install pyserial）。
不依赖项目内任何模块，可单独拷贝运行。

用法（从 software/ 目录运行）：
    python3 tools/write_baseline.py                # 实际写入
    python3 tools/write_baseline.py --dry-run      # 只显示差异
    python3 tools/write_baseline.py --port /dev/ttyUSB0
"""

import argparse
import sys
import time

import serial
import serial.tools.list_ports


SLAVE_ID = 1
BAUDRATE = 115200

# Modbus 功能码
FC_READ_HOLDING = 0x03
FC_WRITE_SINGLE = 0x06
FC_WRITE_MULTIPLE = 0x10

# 基线参数：来自 2026-05-13 实测的正常驱动器
# 格式：(地址, 期望值, 名称, 32位?)
# 跳过通讯参数(0x0000-0x0002)避免断连风险；跳过命令型寄存器和不保存的运行参数
BASELINE = [
    (0x0003, 0, "Modbus 返回等待时间", False),
    # 电机电气参数（基线为 0，表示未校准；属正常状态）
    (0x000E, 0, "电阻", True),
    (0x0010, 0, "电感", True),
    (0x0012, 0, "反应电动势系数", True),
    (0x0014, 0, "电压", False),
    # 电流参数
    (0x0015, 0, "减速电流 (x100mA)", False),
    (0x0016, 15, "怠机电流 (1.5A)", False),
    (0x0017, 63, "加速电流 (6.3A)", False),
    (0x0018, 41, "运行电流 (4.1A)", False),
    (0x0019, 8, "过载电流 (0.8A)", False),
    # 运动参数
    (0x001A, 4, "细分 (1:16)", False),
    (0x001F, 1, "驱动参数（低速优化开）", False),
    # I/O 配置
    (0x002C, 1, "DI1=负限位", True),
    (0x002E, 0, "DI1 极性不取反", False),
    (0x002F, 0, "输入上拉", False),
    (0x0030, 69905, "输入触发方式", True),
    (0x0034, 0, "I/O端口配置", False),
    (0x0035, 0, "故障安全输出", False),
    (0x0036, 0, "故障安全预定", False),
    (0x0037, 0, "数字量输出", True),
    # 运行模式 & 停机
    (0x0039, 1, "运行模式=位置", False),
    (0x003A, 1, "操作启停=减速停机", False),
    (0x003B, 0, "急停=无减速停机", False),
    (0x003C, 0, "故障=无减速停机", False),
    # 检测
    (0x0043, 0, "失速检测阈值", True),
    # 位置/速度
    (0x0057, 0, "位置最小值", True),
    (0x0059, 0, "位置最大值", True),
    (0x005B, 60, "最大速度 60 Step/s", True),
    (0x005D, 16, "最小速度 16 Step/s", True),
    (0x005F, 600, "加速度 600 Step/s²", True),
    (0x0061, 600, "减速度 600 Step/s²", True),
    # 回零参数
    (0x0069, 0, "原点偏移 0", True),
    (0x006B, 17, "回零方式 17", False),
    (0x006C, 50, "寻找开关速度 50", True),
    (0x006E, 20, "寻找零位速度 20", True),
    (0x0072, 0, "零点回归 禁用", False),
]


# ── Modbus CRC16 ─────────────────────────────────────────────


def crc16(data: bytes) -> int:
    """Modbus CRC16 (多项式 0xA001, 初始值 0xFFFF)"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def append_crc(data: bytes) -> bytes:
    c = crc16(data)
    return data + bytes([c & 0xFF, (c >> 8) & 0xFF])


def verify_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    return crc16(frame[:-2]) == (frame[-2] | (frame[-1] << 8))


# ── Modbus 帧构建/解析 ───────────────────────────────────────


def build_read(slave: int, addr: int, count: int) -> bytes:
    return append_crc(
        bytes(
            [
                slave,
                FC_READ_HOLDING,
                (addr >> 8) & 0xFF,
                addr & 0xFF,
                (count >> 8) & 0xFF,
                count & 0xFF,
            ]
        )
    )


def build_write_single(slave: int, addr: int, value: int) -> bytes:
    v = value & 0xFFFF
    return append_crc(
        bytes(
            [
                slave,
                FC_WRITE_SINGLE,
                (addr >> 8) & 0xFF,
                addr & 0xFF,
                (v >> 8) & 0xFF,
                v & 0xFF,
            ]
        )
    )


def build_write_multiple(slave: int, addr: int, values: list[int]) -> bytes:
    count = len(values)
    body = bytes(
        [
            slave,
            FC_WRITE_MULTIPLE,
            (addr >> 8) & 0xFF,
            addr & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
            count * 2,
        ]
    )
    for v in values:
        body += bytes([(v >> 8) & 0xFF, v & 0xFF])
    return append_crc(body)


def parse_read_response(raw: bytes) -> tuple[list[int] | None, str | None]:
    """读保持寄存器响应解析。返回 (values, error_message)"""
    if len(raw) < 5:
        return None, "帧长不足"
    if not verify_crc(raw):
        return None, "CRC 错误"
    if raw[1] & 0x80:
        return None, f"Modbus 异常码 0x{raw[2]:02X}"
    byte_count = raw[2]
    if len(raw) < 3 + byte_count + 2:
        return None, "数据段不完整"
    values = []
    for i in range(0, byte_count, 2):
        values.append((raw[3 + i] << 8) | raw[4 + i])
    return values, None


def parse_write_response(raw: bytes) -> str | None:
    """写响应解析。返回 error_message 或 None"""
    if len(raw) < 8:
        return "帧长不足"
    if not verify_crc(raw):
        return "CRC 错误"
    if raw[1] & 0x80:
        return f"Modbus 异常码 0x{raw[2]:02X}"
    return None


def combine_32bit(high: int, low: int, signed: bool = False) -> int:
    v = (high << 16) | low
    if signed and v >= 0x80000000:
        v -= 0x100000000
    return v


def split_32bit(value: int) -> tuple[int, int]:
    if value < 0:
        value += 0x100000000
    return (value >> 16) & 0xFFFF, value & 0xFFFF


# ── 串口收发 ─────────────────────────────────────────────────


def transact(port: serial.Serial, frame: bytes, expected_len: int) -> bytes:
    port.reset_input_buffer()
    port.write(frame)
    time.sleep(0.02)
    return port.read(expected_len)


def read_holding(port, addr, count):
    frame = build_read(SLAVE_ID, addr, count)
    expected = 5 + count * 2
    raw = transact(port, frame, expected)
    if not raw:
        return None, "超时"
    return parse_read_response(raw)


def write_single(port, addr, value):
    frame = build_write_single(SLAVE_ID, addr, value)
    raw = transact(port, frame, 8)
    if not raw:
        return "超时"
    return parse_write_response(raw)


def write_32bit(port, addr, value):
    high, low = split_32bit(value)
    frame = build_write_multiple(SLAVE_ID, addr, [high, low])
    raw = transact(port, frame, 8)
    if not raw:
        return "超时"
    return parse_write_response(raw)


# ── 主流程 ───────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    available = [p.device for p in serial.tools.list_ports.comports()]
    usb = [p for p in available if "USB" in p or "ACM" in p]
    port_name = args.port or (usb[0] if usb else None)
    if not port_name:
        print(f"无 USB 串口。可见: {available}")
        sys.exit(1)

    port = serial.Serial(port=port_name, baudrate=BAUDRATE, timeout=0.5)
    print(f"端口: {port_name}  从站: {SLAVE_ID}  {'(dry-run)' if args.dry_run else ''}")
    print(f"基线参数项数: {len(BASELINE)}\n")

    print("[1] 停机/脱机（保证需脱机参数可写）...")
    write_single(port, 0x0051, 0x0000)
    time.sleep(0.1)

    print("\n[2] 检查并写入...")
    diffs = []
    fails = []
    for addr, expected, name, is_32bit in BASELINE:
        count = 2 if is_32bit else 1
        vals, err = read_holding(port, addr, count)
        if err:
            fails.append((addr, name, f"读取失败: {err}"))
            print(f"  ✗ 0x{addr:04X} {name:30s}: 读取失败 - {err}")
            continue
        current = combine_32bit(vals[0], vals[1], signed=True) if is_32bit else vals[0]

        if current == expected:
            print(f"  ✓ 0x{addr:04X} {name:30s}: {current} (一致)")
            continue

        diffs.append((addr, current, expected, name))
        if args.dry_run:
            print(f"  ! 0x{addr:04X} {name:30s}: {current} → {expected}")
            continue

        werr = write_32bit(port, addr, expected) if is_32bit else write_single(port, addr, expected)
        time.sleep(0.05)
        if werr:
            fails.append((addr, name, f"写入失败: {werr}"))
            print(f"  ✗ 0x{addr:04X} {name:30s}: {current} → {expected} 失败 - {werr}")
        else:
            print(f"  ✓ 0x{addr:04X} {name:30s}: {current} → {expected} 已写")

    if diffs and not args.dry_run:
        print("\n[3] 保存到 EEPROM (0x0008 = 0x7376)...")
        err = write_single(port, 0x0008, 0x7376)
        print(f"  ✓ 已保存" if not err else f"  ✗ 保存失败: {err}")
        time.sleep(0.5)
    elif not diffs:
        print("\n[3] 全部一致")
    else:
        print("\n[3] dry-run 模式，未保存")

    print("\n" + "=" * 60)
    if not diffs and not fails:
        print(f"✓ 全部 {len(BASELINE)} 项已匹配基线")
    else:
        print(f"修改 {len(diffs)} 项, 失败 {len(fails)} 项")
        if fails:
            print("\n失败项:")
            for addr, name, msg in fails:
                print(f"  0x{addr:04X} {name}: {msg}")

    port.close()


if __name__ == "__main__":
    main()
