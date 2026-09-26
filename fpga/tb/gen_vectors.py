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
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop_app.demo import DemoDevice
from desktop_app.model import ArraySpec, Config, trajectory_sample
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
        "caps=CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,SCAN_PATHS "
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


# ============================================================================
# 轨迹：节拍表（配置阶段）+ 走步器（运行阶段）
# ============================================================================

TRAJ_FRAC = 16                 # 步进的小数位
LAPS_NUM = 100_000_000         # 一圈拍数 = 10^8 ÷ repeat_millihz（一拍 10 µs）
TICK_US = 10                   # 一拍多少微秒
MIN_WEIGHT = 200               # 协议里的 0.1 mm 最低权重（0.5 µm 单位）
BLANK_BUDGET = 1_000_000_000   # 协议：blank_us × 笔数 × repeat_millihz 必须小于它
PT_SLOTS = 256                 # 每个用例在点表向量里占多少行
STK_SLOTS = 32                 # 每个用例在段表向量里占多少行

# 名字、草图文本（微米）、重复频率（毫赫兹）、抬笔时长（微秒）、是否应当通过
# 最后那个用例是一笔 100 mm 的直线接 200 个 1 µm 的小台阶：重复频率 200 Hz 时
# 小台阶连一拍都分不到，应当整份拒绝（而不是偷偷把图形拉长）。
_TOO_FINE = "0:0,100000:0" + "".join(",100000:%d" % i for i in range(1, 201))

TRAJ_CASES = [
    ("two_strokes",    "0:0,10000:0|0:5000,10000:5000",            40000, 2000, 1),
    ("square",         "0:0,10000:0,10000:10000,0:10000,0:0",      40000, 2000, 1),
    ("one_dot_stroke", "0:0,10000:0|5000:5000",                    40000, 2000, 1),
    ("short_strokes",  "0:0,20:0|0:0,20:0",                        20000, 2000, 1),
    ("many_segments",  "0:0,10000:0,10000:10000|0:0,5000:0,5000:5000,0:5000,0:0",
                                                                   40000, 2000, 1),
    # 拒绝：抬笔时间已经把一圈占满（协议里那条 10^9 的规则）
    ("reject_blank",   "0:0,1000:0",                               200000, 100000, 0),
    # 拒绝：算下来每个点分到的拍数不足 1（严格来说 T 必须大于 N×B）
    ("reject_no_time", "0:0,1000:0",                                99999, 10000, 0),
    # 拒绝：图形太碎、重复频率太快，1 µm 的小段连一拍都分不到
    ("reject_too_fine", _TOO_FINE,                                 200000, 100, 0),
]


def _sign_step(delta, magnitude):
    return magnitude if delta >= 0 else -magnitude


def _round_div(num, den):
    """四舍五入的整数除法（和 RTL 里「加半个除数」的做法一致）。"""
    return (num + den // 2) // den


def traj_plan_expect(scan_paths, repeat_millihz, blank_us):
    """按 fpga/多段草图语义设计.md 的规则，算出 RTL 应当产出的节拍表。

    返回 (ok, laps, blank_beats, moves)；moves 的每一项是
    (拍数, x 步进, y 步进, 是否扫描, 第几笔)，步进是定点数、低 16 位是小数。
    这份 Python 是照着**文档里的规则**写的（不是照抄 RTL），用来和 RTL 对拍。
    """
    paths = [[(2 * x, 2 * y) for x, y in path]      # 微米 -> 0.5 微米单位
             for path in Config(shape="CUSTOM", scan_paths=scan_paths,
                                repeat_millihz=repeat_millihz,
                                blank_us=blank_us).strokes_um()]
    laps = LAPS_NUM // repeat_millihz
    blank_beats = blank_us // TICK_US
    strokes = len(paths)
    if strokes == 0 or laps == 0:
        return False, laps, blank_beats, []
    if blank_us * strokes * repeat_millihz >= BLANK_BUDGET:
        return False, laps, blank_beats, []
    if laps <= blank_beats * strokes:
        return False, laps, blank_beats, []
    active = laps - blank_beats * strokes

    raw = []
    for path in paths:
        total = 0
        for (x0, y0), (x1, y1) in zip(path, path[1:]):
            total += math.isqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
        raw.append(total)
    weights = [max(value, MIN_WEIGHT) for value in raw]     # 协议：不足 0.1 mm 按 0.1 mm
    total_weight = sum(weights)

    moves = []
    rem = 0                                                  # 笔与笔之间带过去的余数
    for index, path in enumerate(paths):
        beats, rem = divmod(active * weights[index] + rem, total_weight)
        if len(path) < 2:
            if beats:                                        # 单点笔：原地停留
                moves.append((beats, 0, 0, 1, index))
        else:
            rem_seg = 0                                      # 笔内段与段之间带过去的余数
            for (x0, y0), (x1, y1) in zip(path, path[1:]):
                dx, dy = x1 - x0, y1 - y0
                length = math.isqrt(dx * dx + dy * dy)
                seg, rem_seg = divmod(beats * length + rem_seg, raw[index])
                if seg == 0:                                 # 连一拍都分不到：整份拒绝
                    return False, laps, blank_beats, []
                moves.append((seg,
                              _sign_step(dx, _round_div(abs(dx) << TRAJ_FRAC, seg)),
                              _sign_step(dy, _round_div(abs(dy) << TRAJ_FRAC, seg)),
                              1, index))
        # 抬笔跳转段：从这一笔末点直线走到下一笔（最后一笔回到第一笔）的首点
        last = path[-1]
        following = paths[(index + 1) % strokes][0]
        dx, dy = following[0] - last[0], following[1] - last[1]
        moves.append((blank_beats,
                      _sign_step(dx, _round_div(abs(dx) << TRAJ_FRAC, blank_beats)),
                      _sign_step(dy, _round_div(abs(dy) << TRAJ_FRAC, blank_beats)),
                      0, index))
    assert sum(move[0] for move in moves) == laps, "一圈的拍数必须正好"
    return True, laps, blank_beats, moves


def _speed(move):
    """这一行每拍大概走多远（两轴相加，单位 0.5 µm），用来定比对容差。"""
    return (abs(move[1]) + abs(move[2])) >> TRAJ_FRAC


def traj_samples(scan_paths, repeat_millihz, blank_us, laps, moves):
    """每一拍应当在哪：时间线由节拍表给出，位置由参考实现（model.py）给出。

    每个样本是 (拍号, x, y, 是否扫描, 第几笔, 容差, 是否严格)。
    坐标单位 0.5 µm。容差 = 拍数取整造成的偏差上限（最多差一拍的路程）。
    """
    cfg = Config(shape="CUSTOM", scan_paths=scan_paths,
                 repeat_millihz=repeat_millihz, blank_us=blank_us)
    bounds = []                       # 每一行的起止拍号
    cumulative = 0
    for move in moves:
        bounds.append((cumulative, cumulative + move[0]))
        cumulative += move[0]

    # 采样：每 13 拍一个，外加每个换行点前后各 2 拍，以及开头和结尾几拍
    wanted = set(range(1, laps + 1, 13))
    wanted |= set(range(1, min(laps, 6) + 1))
    for _, end in bounds:
        for offset in range(-2, 3):
            if 1 <= end + offset <= laps:
                wanted.add(end + offset)
    for offset in range(3):
        if laps - offset >= 1:
            wanted.add(laps - offset)

    samples = []
    for beat in sorted(wanted):
        row = 0
        while row + 1 < len(bounds) and beat >= bounds[row][1]:
            row += 1
        speed = max(_speed(moves[i]) for i in (row - 1, row, row + 1)
                    if 0 <= i < len(moves))
        ((x, y, _), scan, stroke) = trajectory_sample(cfg, beat * TICK_US * 1e-6)
        near = any(abs(beat - end) <= 3 for _, end in bounds)
        # 参考实现返回的是毫米，先换成微米（×1000），再换成 0.5 微米单位（×2）
        samples.append((beat, int(round(x * 2000)), int(round(y * 2000)),
                        scan, stroke, 3 + speed, 0 if near else 1))
    assert all(samples[i][0] < samples[i + 1][0] for i in range(len(samples) - 1))
    return samples


# 端到端用例（tb_hap2_top 用）：一条一条真实报文，按顺序发给板子
TOP_SKETCH  = "1000:2000,11000:2000|1000:7000,11000:7000"
TOP_TOOFINE = "0:0,100000:0" + "".join(",100000:%d" % i for i in range(1, 61))


def build_top_cases(device):
    """端到端测试要发的报文（真实 CRC）。返回 [(名字, 报文, 说明)]。"""
    dev = dict(device.array.wire())

    def cfg(seq, **over):
        fields = dict(Config(**over).wire())
        fields.update(dev)
        return encode("CMD", seq, "CONFIG", **fields)

    return [
        ("hello", encode("CMD", 1, "HELLO"),
         "握手：应答里应当声明 SCAN_PATHS"),
        ("config_sketch", cfg(2, shape="CUSTOM", scan_paths=TOP_SKETCH,
                              repeat_millihz=40000, blank_us=2000, level=30),
         "带两笔草图的配置：解析 + 编译节拍表，都过了才回 ACK"),
        ("start", encode("CMD", 3, "START"), "启动：走步器开始走"),
        ("pause", encode("CMD", 4, "PAUSE"), "暂停：位置冻住，输出关掉"),
        ("resume", encode("CMD", 5, "START"), "从暂停恢复：接着走，不回到起点"),
        ("stop", encode("CMD", 6, "STOP"), "停止：回到起点"),
        ("config_toofine", cfg(7, shape="CUSTOM", scan_paths=TOP_TOOFINE,
                               repeat_millihz=200000, blank_us=100),
         "碎到分不到一拍的图形：整份拒绝，版本号不动"),
        ("snap", encode("CMD", 8, "SNAP"), "要一份状态快照"),
        # 预设图形（CIRCLE 等）的轨迹还没做：配置接受，但没有节拍表，
        # 启动之后焦点原地不动、扫描开关保持 0。这一条就是把现状钉住，
        # 等相位那一轮做完再改期望值。
        ("config_circle", cfg(9, shape="CIRCLE", radius_um=20000,
                              repeat_millihz=40000, level=30),
         "预设图形：配置通过，轨迹暂不可用"),
        ("start_preset", encode("CMD", 10, "START"), "启动预设图形：焦点原地不动"),
        ("stop_preset", encode("CMD", 11, "STOP"), "停止"),
        ("ping", encode("CMD", 12, "PING"), "探活：只回 ACK"),
    ]


def build_traj_vectors():
    """写出轨迹用例的向量文件，返回给人看的清单。"""
    hdr0_lines, hdr1_lines = [], []
    ptx_lines, pty_lines = [], []
    stks_lines, stkl_lines = [], []
    mv_lines, gd_lines, gd2_lines = [], [], []
    listing = []
    mv_off = 0
    gd_off = 0
    for name, text, repeat, blank_us, expect_ok in TRAJ_CASES:
        paths = Config(shape="CUSTOM", scan_paths=text, repeat_millihz=repeat,
                       blank_us=blank_us).strokes_um()
        points = [point for path in paths for point in path]
        ok, laps, blank_beats, moves = traj_plan_expect(text, repeat, blank_us)
        assert ok == bool(expect_ok), f"{name} 的通过/拒绝和预期不一致"
        samples = traj_samples(text, repeat, blank_us, laps, moves) if ok else []

        for i in range(PT_SLOTS):                      # 点表：这一路占 PT_SLOTS 行
            if i < len(points):
                ptx_lines.append("%06X" % (2 * points[i][0] & 0x1FFFFF))
                pty_lines.append("%06X" % (2 * points[i][1] & 0x1FFFFF))
            else:
                ptx_lines.append("000000")
                pty_lines.append("000000")
        for i in range(STK_SLOTS):                     # 段表：这一路占 STK_SLOTS 行
            if i < len(paths):
                stks_lines.append("%03X" % sum(len(p) for p in paths[:i]))
                stkl_lines.append("%03X" % len(paths[i]))
            else:
                stks_lines.append("000")
                stkl_lines.append("000")

        # 节拍表：每行 24 个十六进制位 = 拍数(24) 步进x(32) 步进y(32) 扫描(1) 笔号(6)
        for beats, step_x, step_y, scan, stroke in moves:
            mv_lines.append("%06X%08X%08X%02X"
                            % (beats & 0xFFFFFF, step_x & 0xFFFFFFFF,
                               step_y & 0xFFFFFFFF, (scan << 6) | stroke))
        for beat, x, y, scan, stroke, tol, strict in samples:
            gd_lines.append("%04X%06X%06X" % (beat, x & 0xFFFFFF, y & 0xFFFFFF))
            gd2_lines.append("%04X%02X"
                             % (tol & 0xFFFF, (strict << 7) | (scan << 6) | stroke))

        listing.append((name, ok, len(points), len(paths), len(moves),
                        laps, blank_beats, len(samples)))
        # 表头：8 个 16 位字段 = 段数 点数 抬笔拍数 行数 样本数 表偏移 样本偏移 是否通过
        hdr0_lines.append("%04X%04X%04X%04X%04X%04X%04X%04X"
                          % (len(paths), len(points), blank_beats, len(moves),
                             len(samples), mv_off, gd_off, 1 if ok else 0))
        # 另一张表头：重复频率、抬笔时长、一圈拍数（都是 32 位）
        hdr1_lines.append("%08X%08X%08X" % (repeat, blank_us, laps))
        mv_off += len(moves)
        gd_off += len(samples)

    for filename, lines in (("traj_hdr0.mem", hdr0_lines), ("traj_hdr1.mem", hdr1_lines),
                            ("traj_ptx.mem", ptx_lines), ("traj_pty.mem", pty_lines),
                            ("traj_stks.mem", stks_lines), ("traj_stkl.mem", stkl_lines),
                            ("traj_moves.mem", mv_lines), ("traj_gold.mem", gd_lines),
                            ("traj_gold2.mem", gd2_lines)):
        (VECTOR_DIR / filename).write_text("\n".join(lines) + "\n", encoding="ascii")
    return listing


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

    # ---- 轨迹（节拍表 + 走步器）----
    traj_list = build_traj_vectors()
    print("轨迹：%d 个用例" % len(traj_list))
    for name, ok, npt, nst, nmv, laps, blank, nsmp in traj_list:
        print("  %-16s %s  点 %-3d 笔 %-2d 行 %-3d 一圈 %-6d 拍  抬笔 %-4d 拍  样本 %d"
              % (name, "通过" if ok else "拒绝", npt, nst, nmv, laps, blank, nsmp))

    # ---- 端到端（整条通路，对着串口线）----
    tp = bytearray()
    t_lines = []
    top_cases = build_top_cases(device)
    for name, frame, note in top_cases:
        offset = len(tp)
        tp += frame
        t_lines.append("%03X %03X" % (offset, len(frame)))
    write_bytes(VECTOR_DIR / "top_packets.mem", bytes(tp))
    (VECTOR_DIR / "top_plan.mem").write_text("\n".join(t_lines) + "\n", encoding="ascii")
    print("端到端：%d 条报文，%d 字节" % (len(t_lines), len(tp)))
    for name, frame, note in top_cases:
        print("  %-16s %3d 字节  %s" % (name, len(frame), note))

    summary = {"frame_cases": len(plan_lines), "field_cases": len(fplan_lines),
               "cmd_cases": len(cplan_lines),
               "tx_expect": len(tx_lines),
               "scan_cases": len(s_lines),
               "traj_cases": len(traj_list),
               "top_cases": len(t_lines),
               "field_order": ["offset", "bytes", "expect_ok", "expect_verb",
                               "expect_err", "expect_seen_mask"]}
    (VECTOR_DIR / "cases.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写入", VECTOR_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
