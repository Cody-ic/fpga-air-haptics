"""生成串口接收模块的仿真向量。

复用 `desktop_app.protocol` 生成真实的 HAP3 报文，避免手抄字节出错。
有些用例（重复字段、非法字符）用参考编码器造不出来，就用 craft() 手工拼
正文再补上正确的 CRC，这样测的仍是「CRC 正确、内容有问题」的真实场景。

执行：

    python fpga/tb/gen_vectors.py

输出（`fpga/tb/vectors/`）：

- `packets.mem` / `plan.mem`：整帧级用例（看 CRC 与行长）
- `field_packets.mem` / `field_plan.mem`：字段级用例（看帧头、错误分类、字段表）
- `cases.json`：给人看的用例清单
"""

from __future__ import annotations

import binascii
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop_app.demo import DemoDevice
from desktop_app.model import ArraySpec, Config
from desktop_app.protocol import encode

HERE = Path(__file__).resolve().parent
VECTOR_DIR = HERE / "vectors"
MODE_CONTINUOUS = 0
MODE_FRAGMENTED = 1

# 字段编号（必须与 hap2_field_parse.v 里的 F_* 一致）
F_CARRIER_HZ = 0
F_LEVEL = 8
F_SHAPE = 9
# CONFIG 必须带齐的 18 个字段（第 16 位是 MODE 的 value，不算）
CONFIG_FIELDS = 0x6FFFF

# 命令编号（必须与 hap2_field_parse.v 里的 V_* 一致）
V_HELLO, V_PING, V_CONFIG, V_NONE = 0, 1, 2, 0xF

# 错误分类（必须与 hap2_field_parse.v 里的 E_* 一致）
E_NONE, E_CHAR, E_DUP, E_RANGE, E_MISSING = 0, 3, 4, 6, 8

# 应答类型与命令层错误码（必须与 hap2_cmd.v 里的 C_* 一致）
ACK, ERR = 0, 1
C_NONE = 0
C_BUSY = 1
C_LOCAL_CONTROL = 2
C_BAD_CONFIG = 3
C_HARDWARE_MISMATCH = 5
C_NOT_RUNNING = 6
C_UNKNOWN_COMMAND = 8
C_HANDSHAKE_NEEDED = 9

# 控制模式与运行状态（必须与 hap2_cmd.v 里的 M_* / R_* 一致）
MODE_LOCAL, MODE_REMOTE = 0, 1
RUN_IDLE, RUN_RUNNING, RUN_PAUSED = 0, 1, 2


def content_len(frame: bytes) -> int:
    """协议里「行长」的定义：去掉换行、并剥掉行末 CR 之后的字节数。"""
    body = frame[:-1] if frame.endswith(b"\n") else frame
    if body.endswith(b"\r"):
        body = body[:-1]
    return len(body)


def craft(body: str) -> bytes:
    """用正确的 CRC 包住一段手工拼的正文。"""
    raw = body.encode("ascii")
    return raw + ("*%04X" % binascii.crc_hqx(raw, 0xFFFF)).encode("ascii") + b"\n"


def config_pairs(device):
    """参考实现会发的那 16 个字段，顺序与 Config.wire() 一致。"""
    fields = dict(Config(shape="CIRCLE", radius_um=20000).wire())
    fields.update(device.array.wire())
    return [(key, str(value)) for key, value in fields.items()]


def make_body(seq: int, verb: str, pairs) -> str:
    parts = ["HAP3", "CMD", str(seq), verb] + [f"{key}={value}" for key, value in pairs]
    return " ".join(parts)


def build_frame_cases(device):
    hello_lf = encode("CMD", 1, "HELLO")
    hello = hello_lf[:-1] + b"\r\n"                       # 换成 CRLF，测剥 CR
    ping = encode("CMD", 2, "PING")
    bad_crc = bytearray(ping)
    bad_crc[-2] = ord("0") if ping[-2] != ord("0") else ord("1")
    no_star = b"HAP3 CMD 4 PING\n"
    short_hex = b"HAP3 CMD 5 X*AB\n"
    config = encode("CMD", 6, "CONFIG",
                    **dict(**Config(shape="CIRCLE", radius_um=20000).wire(),
                           **device.array.wire()))
    return [
        ("hello_crlf", hello, 1, MODE_CONTINUOUS, 0x15, "带 CRLF 的握手命令，应通过"),
        ("ping_lf", ping, 1, MODE_CONTINUOUS, 0x14, "只有 LF 的探活命令，应通过"),
        ("bad_crc", bytes(bad_crc), 0, MODE_CONTINUOUS, 0x14, "校验码被改坏，应判错"),
        ("no_star", no_star, 0, MODE_CONTINUOUS, 0x0F, "整行没有星号，应判错"),
        ("short_hex", short_hex, 0, MODE_CONTINUOUS, 0x0F, "星号后只有 2 位校验码，应判错"),
        ("config", config, 1, MODE_CONTINUOUS, 269, "真实的配置命令（HAP3 是 270 字节），应通过"),
        ("fragmented", hello, 1, MODE_FRAGMENTED, 0x15, "同一个握手命令，字节之间带空档"),
    ]


def build_field_cases(device):
    base = config_pairs(device)
    cases = []

    def add(name, seq, verb, pairs, expect_ok, expect_verb, expect_err, expect_seen, note):
        frame = craft(make_body(seq, verb, pairs))
        cases.append((name, frame, expect_ok, expect_verb, expect_err, expect_seen, note))

    # 基准：与参考编码器逐字节一致（下面会断言）
    add("field_config_ok", 6, "CONFIG", base, 1, V_CONFIG, E_NONE, CONFIG_FIELDS,
        "标准配置命令，应通过且 16 个字段齐全")
    add("field_dup_key", 7, "CONFIG", base + [("level", "30")], 0, V_CONFIG, E_DUP,
        CONFIG_FIELDS, "level 出现了两次，应报重复字段")
    add("field_range", 8, "CONFIG",
        [("carrier_hz", "90000")] + [p for p in base if p[0] != "carrier_hz"],
        0, V_CONFIG, E_RANGE, 0x0001, "载波 90000 Hz 超出 20000~80000，应报越界")
    add("field_badchar", 9, "CONFIG",
        [(k, "3%" if k == "level" else v) for k, v in base],
        0, V_CONFIG, E_CHAR, 0x01FF, "level 的值里出现百分号，应报非法字符")
    add("field_missing", 10, "CONFIG",
        [p for p in base if p[0] != "level"],
        0, V_CONFIG, E_MISSING, CONFIG_FIELDS & ~(1 << F_LEVEL),
        "缺少 level 字段，应报字段不全")
    add("field_unknown_key", 11, "CONFIG", base + [("foo", "1")],
        1, V_CONFIG, E_NONE, CONFIG_FIELDS, "多了一个不认识的键，应忽略且仍然通过")
    add("field_unknown_verb", 12, "REBOOT", [],
        1, V_NONE, E_NONE, 0x0000, "不认识的命令，结构上仍然合法")
    add("field_hello", 13, "HELLO", [],
        1, V_HELLO, E_NONE, 0x0000, "握手命令不带字段")
    add("field_ping", 14, "PING", [],
        1, V_PING, E_NONE, 0x0000, "探活命令不带字段")
    add("field_triangle", 15, "CONFIG",
        [(k, "TRIANGLE" if k == "shape" else v) for k, v in base],
        1, V_CONFIG, E_NONE, CONFIG_FIELDS, "三角形预设，图形名应被认出")

    # 自检：手工拼出来的基准报文必须和参考编码器完全一致
    assert cases[0][1] == encode("CMD", 6, "CONFIG", **dict(base)), "基准报文与参考编码器不一致"
    return cases


def build_cmd_cases(device):
    """命令裁决用例：每条命令在什么状态下允许、拒绝时回什么错误码。

    每个用例 8 个值：偏移 字节数 应答类型 错误码 模式 运行状态 版本增量 要不要跟状态
    """
    base = config_pairs(device)
    cases = []

    def add(name, frame, kind, code, mode, run, rev_delta, want_state, note):
        cases.append((name, frame, kind, code, mode, run, rev_delta, want_state, note))

    def cfg(seq, **overrides):
        pairs = [(k, str(overrides.get(k, v))) for k, v in base]
        return craft(make_body(seq, "CONFIG", pairs))

    add("cfg_before_hello", cfg(10), 1, C_HANDSHAKE_NEEDED, MODE_REMOTE, RUN_IDLE, 0, 0,
        "没握手就发配置，应拒绝")
    add("hello", encode("CMD", 11, "HELLO"), 0, C_NONE, MODE_REMOTE, RUN_IDLE, 0, 1,
        "握手成功")
    add("config_ok", cfg(12), 0, C_NONE, MODE_REMOTE, RUN_IDLE, 1, 1,
        "配置合法，应生效并把版本号加一")
    add("config_bad_array", cfg(13, hw_rows=8), 1, C_HARDWARE_MISMATCH, MODE_REMOTE, RUN_IDLE, 0, 0,
        "阵列字段与实际不符，应拒绝且不动版本号")
    add("ping", encode("CMD", 14, "PING"), 0, C_NONE, MODE_REMOTE, RUN_IDLE, 0, 0,
        "探活只回 ACK")
    add("mode_local", encode("CMD", 15, "MODE", value="LOCAL"), 0, C_NONE, MODE_LOCAL, RUN_IDLE, 0, 1,
        "切成本地控制")
    add("config_in_local", cfg(16), 1, C_LOCAL_CONTROL, MODE_LOCAL, RUN_IDLE, 0, 0,
        "本地控制下不许改配置")
    add("start_in_local", encode("CMD", 17, "START"), 1, C_LOCAL_CONTROL, MODE_LOCAL, RUN_IDLE, 0, 0,
        "本地控制下不许电脑启动")
    add("mode_remote", encode("CMD", 18, "MODE", value="REMOTE"), 0, C_NONE, MODE_REMOTE, RUN_IDLE, 0, 1,
        "切回电脑控制")
    add("start", encode("CMD", 19, "START"), 0, C_NONE, MODE_REMOTE, RUN_RUNNING, 0, 1,
        "开始运行")
    add("start_again", encode("CMD", 20, "START"), 1, C_BUSY, MODE_REMOTE, RUN_RUNNING, 0, 0,
        "已经在跑，再启动应报忙")
    add("pause", encode("CMD", 21, "PAUSE"), 0, C_NONE, MODE_REMOTE, RUN_PAUSED, 0, 1,
        "暂停")
    add("pause_again", encode("CMD", 22, "PAUSE"), 1, C_NOT_RUNNING, MODE_REMOTE, RUN_PAUSED, 0, 0,
        "没在跑，暂停应报未运行")
    add("resume", encode("CMD", 23, "START"), 0, C_NONE, MODE_REMOTE, RUN_RUNNING, 0, 1,
        "从暂停恢复")
    add("stop", encode("CMD", 24, "STOP"), 0, C_NONE, MODE_REMOTE, RUN_IDLE, 0, 1,
        "停止并回到待机")
    add("snap", encode("CMD", 25, "SNAP"), 0, C_NONE, MODE_REMOTE, RUN_IDLE, 0, 1,
        "只要一份状态")
    add("unknown_verb", encode("CMD", 26, "REBOOT"), 1, C_UNKNOWN_COMMAND, MODE_REMOTE, RUN_IDLE, 0, 0,
        "不认识的命令")
    add("config_bad_range", cfg(27, carrier_hz=90000), 1, C_BAD_CONFIG, MODE_REMOTE, RUN_IDLE, 0, 0,
        "载波越界，结构层就否掉了")
    return cases


def expected_ack(seq, verb, rev):
    return craft("HAP3 ACK %d %s applied=1 rev=%d" % (seq, verb, rev))


def expected_err(seq, verb, code):
    return craft("HAP3 ERR %d %s code=%s" % (seq, verb, code))


def expected_hello_ack(seq, boot_hex="A1B2C3D4"):
    return craft(
        "HAP3 ACK %d HELLO proto=3 device=FPGA boot=%s simulated=0 hb_ms=3000 "
        "caps=CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE "
        "max_rows=16 max_cols=16 max_channels=256 max_nodes=64 "
        "max_scan_points=256 max_strokes=32 "
        "hw_rows=4 hw_cols=4 hw_pitch_um=10000 mapping=ROW_MAJOR_XY "
        "x_min_um=-100000 x_max_um=100000 y_min_um=-100000 y_max_um=100000 "
        "z_min_um=20000 z_max_um=300000" % (seq, boot_hex))


def build_tx_expect():
    """板子应当发出的应答（逐字节对照用）。序号与 cmd 用例一致。"""
    return [
        ("hello_ack", expected_hello_ack(11)),
        ("config_ack", expected_ack(12, "CONFIG", 1)),
        ("start_local_err", expected_err(17, "START", "LOCAL_CONTROL")),
    ]


def build_scan_cases():
    """多段草图解析用例。

    期望的坐标是 **0.5 微米单位**（点表就是这个单位，字符串里是微米）。
    不通过的用例用 -1 表示"这项不检查"。
    """
    raw = [
        ("one_stroke", "0:0,10:5,20:0", 1),
        ("two_strokes", "0:0,100:0|0:50,100:50", 1),
        ("negative", "-100:-200,300:-400", 1),
        ("dup_point", "0:0,0:0", 0),
        ("empty_stroke", "0:0||1:1", 0),
        ("out_of_range", "0:0,400000:0", 0),
        ("missing_y", "0:0,1", 0),
        ("bad_char", "0:0,1;2", 0),
        ("too_many_points", ",".join("%d:%d" % (i, i) for i in range(257)), 0),
    ]
    cases = []
    for name, text, ok in raw:
        if ok:
            strokes = [s.split(",") for s in text.split("|")]
            pts = [tuple(map(int, tok.split(":"))) for s in strokes for tok in s]
            cases.append((name, text, 1, len(pts), len(strokes),
                          pts[0][0] * 2, pts[0][1] * 2, pts[-1][0] * 2, pts[-1][1] * 2))
        else:
            cases.append((name, text, 0, 0, 0, 0, 0, 0, 0))
    return cases


def write_bytes(path: Path, blob: bytes):
    path.write_text("".join("%02X\n" % byte for byte in blob), encoding="ascii")


def main():
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    device = DemoDevice(array=ArraySpec(4, 4, 10000, "ROW_MAJOR_XY"))

    # ---- 整帧级 ----
    packets = bytearray()
    plan_lines = []
    for name, frame, ok, mode, length, note in build_frame_cases(device):
        offset = len(packets)
        packets += frame
        assert content_len(frame) == length, f"{name} 行长与预期不符"
        plan_lines.append("%03X %03X %03X %X %X" % (offset, len(frame), length, ok, mode))
    write_bytes(VECTOR_DIR / "packets.mem", bytes(packets))
    (VECTOR_DIR / "plan.mem").write_text("\n".join(plan_lines) + "\n", encoding="ascii")
    print("整帧级：%d 个用例，%d 字节" % (len(plan_lines), len(packets)))

    # ---- 字段级 ----
    fpackets = bytearray()
    fplan_lines = []
    for name, frame, ok, verb, err, seen, note in build_field_cases(device):
        offset = len(fpackets)
        fpackets += frame
        fplan_lines.append("%03X %03X %X %X %X %05X"
                           % (offset, len(frame), ok, verb, err, seen))
    write_bytes(VECTOR_DIR / "field_packets.mem", bytes(fpackets))
    (VECTOR_DIR / "field_plan.mem").write_text("\n".join(fplan_lines) + "\n", encoding="ascii")
    print("字段级：%d 个用例，%d 字节" % (len(fplan_lines), len(fpackets)))

    # ---- 命令级 ----
    cpackets = bytearray()
    cplan_lines = []
    cmd_cases = build_cmd_cases(device)
    for name, frame, kind, code, mode, run, rev_delta, want_state, note in cmd_cases:
        offset = len(cpackets)
        cpackets += frame
        # 序号就是报文里第三个以空格分隔的字段，取出来供测试台核对「应答是否原样抄回」
        frame_seq = int(frame.split(b" ")[2])
        cplan_lines.append("%03X %03X %X %X %X %X %X %X %02X"
                           % (offset, len(frame), kind, code, mode, run,
                              rev_delta, want_state, frame_seq))
    write_bytes(VECTOR_DIR / "cmd_packets.mem", bytes(cpackets))
    (VECTOR_DIR / "cmd_plan.mem").write_text("\n".join(cplan_lines) + "\n", encoding="ascii")
    print("命令级：%d 个用例，%d 字节" % (len(cplan_lines), len(cpackets)))
    for name, frame, kind, code, mode, run, rev_delta, want_state, note in cmd_cases:
        print("  %-18s %-3s 错误码 %-2d %s" % (name, "ACK" if kind == 0 else "ERR", code, note))

    # ---- 板子应答的逐字节对照 ----
    txp = bytearray()
    tx_lines = []
    for name, frame in build_tx_expect():
        offset = len(txp)
        txp += frame
        tx_lines.append("%03X %03X" % (offset, len(frame)))
    write_bytes(VECTOR_DIR / "tx_expect.mem", bytes(txp))
    (VECTOR_DIR / "tx_expect_plan.mem").write_text("\n".join(tx_lines) + "\n", encoding="ascii")
    print("应答对照：%d 条，%d 字节" % (len(tx_lines), len(txp)))
    for name, frame in build_tx_expect():
        print("  %-16s %3d 字节  %s" % (name, len(frame), frame.decode("ascii").rstrip()))

    # ---- 多段草图解析 ----
    sp = bytearray()
    s_lines = []
    scan_cases = build_scan_cases()
    for name, text, ok, npt, nst, x0, y0, x1, y1 in scan_cases:
        raw = text.encode("ascii")
        offset = len(sp)
        sp += raw
        # 坐标写成 16 位补码十六进制（$readmemh 只认十六进制，负号不认）
        s_lines.append("%03X %03X %X %03X %X %04X %04X %04X %04X"
                       % (offset, len(raw), ok, npt, nst,
                          x0 & 0xFFFF, y0 & 0xFFFF, x1 & 0xFFFF, y1 & 0xFFFF))
    write_bytes(VECTOR_DIR / "scan_packets.mem", bytes(sp))
    (VECTOR_DIR / "scan_plan.mem").write_text("\n".join(s_lines) + "\n", encoding="ascii")
    print("多段草图：%d 个用例，%d 字节" % (len(s_lines), len(sp)))
    for name, text, ok, npt, nst, x0, y0, x1, y1 in scan_cases:
        shown = text if len(text) <= 46 else text[:42] + "..."
        print("  %-16s %s  点数 %-4s 段数 %-3s  %s"
              % (name, "通过" if ok else "拒绝", npt if ok else "-", nst if ok else "-", shown))

    summary = {"frame_cases": len(plan_lines), "field_cases": len(fplan_lines),
               "cmd_cases": len(cplan_lines),
               "tx_expect": len(tx_lines),
               "scan_cases": len(s_lines),
               "field_order": ["offset", "bytes", "expect_ok", "expect_verb",
                               "expect_err", "expect_seen_mask"]}
    (VECTOR_DIR / "cases.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写入", VECTOR_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
