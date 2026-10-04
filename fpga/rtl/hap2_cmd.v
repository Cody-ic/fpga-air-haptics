`timescale 1ns/1ps

// HAP2 命令裁决：决定一条命令「此刻允不允许执行」「回什么」「改不改状态」。
//
// 输入是接收通路交来的一帧（帧头 + 逐个字段），输出是：
//   - 一条应答（ACK 还是 ERR、错误码是什么、要不要跟一份 STATE）
//   - 状态变化（控制模式、运行状态）
//   - 配置是否被原子替换（CONFIG 全部检查通过才换，换完 rev 加一）
//
// 八条命令与它们的允许条件（协议第 4 节）：
//   HELLO  任何时候，未握手前只认它
//   PING   握手后，只回 ACK 不跟 STATE
//   CONFIG 握手后 + REMOTE + IDLE；还要与实际阵列核对
//   MODE   握手后 + IDLE，参数 value 必须是 LOCAL 或 REMOTE
//   START  握手后 + REMOTE + (IDLE 或 PAUSED)
//   PAUSE  握手后 + REMOTE + RUNNING
//   STOP   握手后，任何模式都行，优先级最高
//   SNAP   握手后，只要一份状态，什么都不改
//
// 配置用「影子寄存器 + 一次性替换」：字段流先写进影子，全部检查通过才整体
// 拷进生效寄存器。这样不会出现「换了一半」的中间状态。
module hap2_cmd #(
    parameter integer ADDR_W = 13      // 行缓冲 8192 字节，地址 13 位
) (
    input  wire            clk,
    input  wire            rst_n,

    // ---- 来自接收通路 ----
    input  wire            frame_ok,      // 单拍脉冲：整帧结构合法
    input  wire            frame_bad,     // 单拍脉冲：结构层已判错
    input  wire            header_ok,     // 帧头是否认出来了
    input  wire [1:0]      kind,          // 帧类型，0=CMD（只对电脑发的命令作应答）
    input  wire [15:0]     seq,
    input  wire [3:0]      verb,
    input  wire            verb_unknown,
    input  wire            field_valid,   // 单拍脉冲：一个字段解析完成
    input  wire [4:0]      field_id,
    input  wire [31:0]     field_value,
    input  wire [2:0]      field_enum,
    input  wire [ADDR_W:0] field_off,
    input  wire [ADDR_W:0] field_size,
    input  wire [18:0]     seen_mask,

    // ---- 板子实际阵列（握手时声明，用于核对 CONFIG）----
    input  wire [31:0]     array_rows,
    input  wire [31:0]     array_cols,
    input  wire [31:0]     array_pitch_um,

    // ---- 心跳看门狗 ----
    input  wire            heartbeat_lost, // 单拍脉冲：主机失联

    // ---- 轨迹子系统（多段草图 → 节拍表 → 走步器）----
    output reg             traj_parse_start,    // 单拍：解析草图文本
    output reg             traj_plan_start,     // 单拍：编译节拍表
    output reg             traj_shape_start,    // 单拍：生成预设图形的点表
    output reg             traj_preset,         // 1 = 这次生效的是预设图形（不是草图）
    input  wire            traj_shape_done,     // 单拍：预设图形的点表生成完了
    output reg             traj_txt_commit,     // 单拍：配置被接受，原文可以对外公布
    output reg             traj_txt_none,       // 这份配置没有草图（预设图形或 NONE）
    input  wire            traj_parse_ok,       // 单拍：原文解析通过
    input  wire            traj_parse_bad,      // 单拍：原文不是合法草图
    input  wire            traj_none,           // 这次解析的对象是 NONE（没有草图）
    input  wire            traj_plan_done,      // 单拍：节拍表算好了
    input  wire            traj_plan_fault,     // 单拍：这份图形／配置做不出节拍表
    output wire [ADDR_W:0] traj_src_off,        // 解析用的起点与长度（影子配置里存的）
    output wire [ADDR_W:0] traj_src_len,
    output wire [31:0]     traj_repeat_millihz, // 编译用的是影子配置（编译发生在生效之前）
    output wire [31:0]     traj_blank_us,
    // 生成预设图形要用的是**影子配置**里的形状参数（生成发生在配置生效之前）。
    // 这一点和节拍表用 traj_repeat_millihz / traj_blank_us 是同一个道理：
    // 拿「已生效的旧配置」去生成，会生成出上一种图形。
    output wire [2:0]      traj_shape_kind,
    output wire [31:0]     traj_radius_um,
    output wire [31:0]     traj_cx_um,
    output wire [31:0]     traj_cy_um,
    output reg             walk_start,          // 单拍：从轨迹起点开始走
    output wire            walk_hold,           // 电平：暂停（位置冻结）
    output reg             walk_stop,           // 单拍：停下并回到起点
    output reg             traj_ready,          // 当前配置有一份可用的节拍表

    // ---- 应答 ----
    output reg             reply_valid,    // 单拍脉冲：有一条应答要发
    output reg  [1:0]      reply_kind,     // 0=ACK 1=ERR
    output reg  [3:0]      reply_verb,
    output reg  [15:0]     reply_seq,
    output reg  [3:0]      reply_code,     // 错误码，ACK 时为 C_NONE
    output reg             reply_want_state, // 1 = 应答之后还要跟一份 STATE

    // ---- 运行状态 ----
    output reg  [1:0]      mode,           // 0=LOCAL 1=REMOTE
    output reg  [1:0]      run_state,      // 0=IDLE 1=RUNNING 2=PAUSED 3=FAULT
    output reg  [2:0]      reason,         // 0=NONE 1=HEARTBEAT_TIMEOUT
    output reg  [15:0]     revision,
    output reg             config_changed, // 单拍脉冲：配置刚被原子替换
    output wire            connected_out,  // 已经握过手（顶层据此决定要不要主动上报状态）

    // ---- 已生效的配置 ----
    output reg  [31:0]     cfg_carrier_hz,
    output reg  [31:0]     cfg_phase_steps,
    output reg  [31:0]     cfg_cx_um,
    output reg  [31:0]     cfg_cy_um,
    output reg  [31:0]     cfg_z_um,
    output reg  [31:0]     cfg_radius_um,
    output reg  [31:0]     cfg_repeat_millihz,
    output reg  [31:0]     cfg_mod_hz,
    output reg  [31:0]     cfg_level,
    output reg  [2:0]      cfg_shape,
    output reg  [31:0]     cfg_path_closed,
    output reg  [ADDR_W:0] cfg_path_off,   // 自定义路径在行缓冲里的位置与长度
    output reg  [ADDR_W:0] cfg_path_size,
    output reg  [ADDR_W:0] cfg_scan_off,   // HAP3 多段草图的字符串位置与长度
    output reg  [ADDR_W:0] cfg_scan_size,
    output reg  [31:0]     cfg_blank_us    // HAP3 段间跳转时长（微秒）
);

    // ---------------- 命令编号（与 hap2_field_parse.v 一致）----------------
    localparam [3:0] V_HELLO  = 4'd0;
    localparam [3:0] V_PING   = 4'd1;
    localparam [3:0] V_CONFIG = 4'd2;
    localparam [3:0] V_MODE   = 4'd3;
    localparam [3:0] V_START  = 4'd4;
    localparam [3:0] V_PAUSE  = 4'd5;
    localparam [3:0] V_STOP   = 4'd6;
    localparam [3:0] V_SNAP   = 4'd7;

    // ---------------- 字段编号 ----------------
    localparam [4:0] F_CARRIER_HZ  = 5'd0;
    localparam [4:0] F_PHASE_STEPS = 5'd1;
    localparam [4:0] F_CX_UM       = 5'd2;
    localparam [4:0] F_CY_UM       = 5'd3;
    localparam [4:0] F_Z_UM        = 5'd4;
    localparam [4:0] F_RADIUS_UM   = 5'd5;
    localparam [4:0] F_REPEAT      = 5'd6;
    localparam [4:0] F_MOD_HZ      = 5'd7;
    localparam [4:0] F_LEVEL       = 5'd8;
    localparam [4:0] F_SHAPE       = 5'd9;
    localparam [4:0] F_PATH_XY     = 5'd10;
    localparam [4:0] F_PATH_CLOSED = 5'd11;
    localparam [4:0] F_HW_ROWS     = 5'd12;
    localparam [4:0] F_HW_COLS     = 5'd13;
    localparam [4:0] F_HW_PITCH    = 5'd14;
    localparam [4:0] F_MAPPING     = 5'd15;
    localparam [4:0] F_VALUE       = 5'd16;
    localparam [4:0] F_SCAN_PATHS  = 5'd17;
    localparam [4:0] F_BLANK_US    = 5'd18;

    // ---------------- 错误码（协议第 4 节）----------------
    localparam [3:0] C_NONE             = 4'd0;
    localparam [3:0] C_BUSY             = 4'd1;
    localparam [3:0] C_LOCAL_CONTROL    = 4'd2;
    localparam [3:0] C_BAD_CONFIG       = 4'd3;
    // 4 号错误码是 C_OUT_OF_WORKSPACE（越出设备声明的工作空间）。它要等
    // path_xy_um 的坐标串解析做完、能算出轨迹包围盒之后才用得上，这里先留空。
    localparam [3:0] C_HARDWARE_MISMATCH= 4'd5;
    localparam [3:0] C_NOT_RUNNING      = 4'd6;
    localparam [3:0] C_BAD_MODE         = 4'd7;
    localparam [3:0] C_UNKNOWN_COMMAND  = 4'd8;
    localparam [3:0] C_HANDSHAKE_NEEDED = 4'd9;

    // ---------------- 控制模式与运行状态 ----------------
    // 控制模式的编号与字段解析层里 LOCAL / REMOTE 的枚举一致
    localparam [1:0] M_LOCAL   = 2'd0;
    localparam [1:0] M_REMOTE  = 2'd1;
    localparam [1:0] R_IDLE    = 2'd0;
    localparam [1:0] R_RUNNING = 2'd1;
    localparam [1:0] R_PAUSED  = 2'd2;

    localparam [2:0] REASON_NONE = 3'd0;
    localparam [2:0] REASON_HB   = 3'd1;

    // 多段草图的形状编号（和字段解析、应答里的图形名一致）
    localparam [2:0] SH_CUSTOM = 3'd7;

    // 待办配置的三步：解析原文 → 编译节拍表 → 才回 ACK、才把配置换上去。
    // 之所以要「等」，是因为协议里 ACK applied=1 的意思就是「这份配置已经生效」，
    // 而能不能生效要等节拍表算出来才知道（算不出来必须回 BAD_CONFIG）。
    localparam [1:0] P_IDLE  = 2'd0;
    localparam [1:0] P_PARSE = 2'd1;
    localparam [1:0] P_PLAN  = 2'd2;
    localparam [1:0] P_SHAPE = 2'd3;

    reg connected;          // 是否已完成握手
    reg array_bad;          // CONFIG 帧里阵列字段与实际不符
    reg [1:0] mode_value;   // MODE 命令的 value 解出来的模式
    reg [1:0] pending;      // 见 P_*
    reg [3:0] lat_verb;     // 待办配置的应答要用的命令名与序号
    reg [15:0] lat_seq;

    // ---------------- 影子配置寄存器 ----------------
    reg [31:0]     sh_carrier_hz;
    reg [31:0]     sh_phase_steps;
    reg [31:0]     sh_cx_um;
    reg [31:0]     sh_cy_um;
    reg [31:0]     sh_z_um;
    reg [31:0]     sh_radius_um;
    reg [31:0]     sh_repeat_millihz;
    reg [31:0]     sh_mod_hz;
    reg [31:0]     sh_level;
    reg [2:0]      sh_shape;
    reg [31:0]     sh_path_closed;
    reg [ADDR_W:0] sh_path_off;
    reg [ADDR_W:0] sh_path_size;
    reg [ADDR_W:0] sh_scan_off;
    reg [ADDR_W:0] sh_scan_size;
    reg [31:0]     sh_blank_us;

    // ---------------- 组合判定 ----------------
    reg [3:0] dec_err;
    reg       dec_want_state;
    reg       dec_hello;
    reg       dec_mode_set;
    reg [1:0] dec_mode;
    reg       dec_run;
    reg [1:0] dec_run_state;
    reg       dec_commit;

    // 轨迹子系统要用的都是「影子配置」：编译发生在配置生效之前
    assign traj_src_off        = sh_scan_off;
    assign traj_src_len        = sh_scan_size;
    assign traj_repeat_millihz = sh_repeat_millihz;
    assign traj_blank_us       = sh_blank_us;
    assign traj_shape_kind     = sh_shape;
    assign traj_radius_um      = sh_radius_um;
    assign traj_cx_um          = sh_cx_um;
    assign traj_cy_um          = sh_cy_um;
    // 暂停 = 冻结走步器（启动阶段就一直在数节拍）；没有节拍表就没什么可冻结的
    assign walk_hold           = traj_ready && (run_state == R_PAUSED);
    assign connected_out       = connected;

    // 把影子配置整体换上去，并回一条 ACK（+ STATE）。只有解析和编译都过了才调用。
    // ready = 这份配置有没有可用的节拍表（预设图形的轨迹还没做，所以是 0）。
    // rseq / rverb 是这条应答要对上的序号与命令名：立即生效时就是当前帧的，
    // 等编译时是暂存下来的（因为编译要花一毫秒，那时当前帧早过去了）。
    task accept_config;
        input ready;
        input [15:0] rseq;
        input [3:0]  rverb;
        begin
            cfg_carrier_hz     <= sh_carrier_hz;
            cfg_phase_steps    <= sh_phase_steps;
            cfg_cx_um          <= sh_cx_um;
            cfg_cy_um          <= sh_cy_um;
            cfg_z_um           <= sh_z_um;
            cfg_radius_um      <= sh_radius_um;
            cfg_repeat_millihz <= sh_repeat_millihz;
            cfg_mod_hz         <= sh_mod_hz;
            cfg_level          <= sh_level;
            cfg_shape          <= sh_shape;
            cfg_path_closed    <= sh_path_closed;
            cfg_path_off       <= sh_path_off;
            cfg_path_size      <= sh_path_size;
            cfg_scan_off       <= sh_scan_off;
            cfg_scan_size      <= sh_scan_size;
            cfg_blank_us       <= sh_blank_us;
            revision           <= revision + 1'b1;
            config_changed     <= 1'b1;
            traj_ready         <= ready;
            traj_txt_commit    <= 1'b1;    // 原文可以对外公布了（STATE 要回传）
            traj_txt_none      <= (sh_shape != SH_CUSTOM) || traj_none;
            walk_stop          <= 1'b1;        // 新图形一律从起点重新开始
            reply_valid        <= 1'b1;
            reply_kind         <= 2'd0;
            reply_verb         <= rverb;
            reply_seq          <= rseq;
            reply_code         <= C_NONE;
            reply_want_state   <= 1'b1;
        end
    endtask

    // 这份配置不能用：回 ERR，配置一个字都不动，版本号也不动
    task reject_config;
        input [15:0] rseq;
        input [3:0]  rverb;
        begin
            // 注意：这里**不能**动 traj_ready。被拒绝的配置不改变已生效的任何东西，
            // 上一份草图的节拍表仍然是好的（表本身是双缓冲的，编译不会破坏它）。
            walk_stop    <= 1'b1;
            reply_valid  <= 1'b1;
            reply_kind   <= 2'd1;
            reply_verb   <= rverb;
            reply_seq    <= rseq;
            reply_code   <= C_BAD_CONFIG;
            reply_want_state <= 1'b0;
        end
    endtask

    always @* begin
        dec_err        = C_NONE;
        dec_want_state = 1'b1;      // 除了 PING，每条命令之后都跟一份 STATE
        dec_hello      = 1'b0;
        dec_mode_set   = 1'b0;
        dec_mode       = mode;
        dec_run        = 1'b0;
        dec_run_state  = run_state;
        dec_commit     = 1'b0;

        if (frame_bad) begin
            // 结构层已经否掉了这一帧，只回错误码，什么都不改
            dec_err = C_BAD_CONFIG;
        end else if (verb_unknown) begin
            dec_err = C_UNKNOWN_COMMAND;
        end else if (!connected && verb != V_HELLO) begin
            dec_err = C_HANDSHAKE_NEEDED;
        end else begin
            case (verb)
                V_HELLO: begin
                    dec_hello = 1'b1;
                end

                V_PING: begin
                    dec_want_state = 1'b0;          // 探活只回一个 ACK
                end

                V_CONFIG: begin
                    if (mode == M_LOCAL)              dec_err = C_LOCAL_CONTROL;
                    else if (run_state != R_IDLE)     dec_err = C_BUSY;
                    else if (array_bad)               dec_err = C_HARDWARE_MISMATCH;
                    else                              dec_commit = 1'b1;
                end

                V_MODE: begin
                    if (run_state != R_IDLE)          dec_err = C_BUSY;
                    else if (!seen_mask[F_VALUE])     dec_err = C_BAD_MODE;
                    else begin
                        dec_mode_set = 1'b1;
                        dec_mode     = mode_value;
                    end
                end

                V_START: begin
                    if (mode == M_LOCAL)              dec_err = C_LOCAL_CONTROL;
                    else if (run_state == R_IDLE || run_state == R_PAUSED) begin
                        dec_run       = 1'b1;
                        dec_run_state = R_RUNNING;
                    end else begin
                        dec_err = C_BUSY;
                    end
                end

                V_PAUSE: begin
                    if (mode == M_LOCAL)              dec_err = C_LOCAL_CONTROL;
                    else if (run_state != R_RUNNING)  dec_err = C_NOT_RUNNING;
                    else begin
                        dec_run       = 1'b1;
                        dec_run_state = R_PAUSED;
                    end
                end

                V_STOP: begin
                    dec_run       = 1'b1;             // 两种模式都有效，优先级最高
                    dec_run_state = R_IDLE;
                end

                V_SNAP: begin
                    // 只要一份状态，什么都不改
                end

                default: dec_err = C_UNKNOWN_COMMAND;
            endcase
        end
    end

    // ---------------- 时序逻辑 ----------------
    always @(posedge clk) begin
        if (!rst_n) begin
            connected      <= 1'b0;
            array_bad      <= 1'b0;
            mode_value     <= M_REMOTE;
            pending        <= P_IDLE;
            lat_verb       <= 4'd0;
            lat_seq        <= 16'd0;
            traj_ready     <= 1'b0;
            traj_parse_start <= 1'b0;
            traj_plan_start  <= 1'b0;
            traj_shape_start <= 1'b0;
            traj_preset      <= 1'b0;
            traj_txt_commit  <= 1'b0;
            traj_txt_none    <= 1'b0;
            walk_start       <= 1'b0;
            walk_stop        <= 1'b0;
            mode           <= M_REMOTE;    // 上电默认听电脑的，但不握手不接受命令
            run_state      <= R_IDLE;      // 上电必须保持输出关闭
            reason         <= REASON_NONE;
            revision       <= 16'd0;
            reply_valid    <= 1'b0;
            reply_kind     <= 2'd0;
            reply_verb     <= 4'd0;
            reply_seq      <= 16'd0;
            reply_code     <= C_NONE;
            reply_want_state <= 1'b0;
            config_changed <= 1'b0;
            cfg_carrier_hz     <= 32'd40000;
            cfg_phase_steps    <= 32'd64;
            cfg_cx_um          <= 32'd0;
            cfg_cy_um          <= 32'd0;
            cfg_z_um           <= 32'd150000;
            cfg_radius_um      <= 32'd20000;
            cfg_repeat_millihz <= 32'd500;
            cfg_mod_hz         <= 32'd200;
            cfg_level          <= 32'd30;
            cfg_shape          <= 3'd3;      // CIRCLE
            cfg_path_closed    <= 32'd1;
            cfg_path_off       <= 0;
            cfg_path_size      <= 0;
            cfg_scan_off       <= 0;
            cfg_scan_size      <= 0;
            cfg_blank_us       <= 32'd2000;
            sh_carrier_hz      <= 32'd40000;
            sh_phase_steps     <= 32'd64;
            sh_cx_um           <= 32'd0;
            sh_cy_um           <= 32'd0;
            sh_z_um            <= 32'd150000;
            sh_radius_um       <= 32'd20000;
            sh_repeat_millihz  <= 32'd500;
            sh_mod_hz          <= 32'd200;
            sh_level           <= 32'd30;
            sh_shape           <= 3'd3;
            sh_path_closed     <= 32'd1;
            sh_path_off        <= 0;
            sh_path_size       <= 0;
            sh_scan_off        <= 0;
            sh_scan_size       <= 0;
            sh_blank_us        <= 32'd2000;
        end else begin
            reply_valid    <= 1'b0;
            config_changed <= 1'b0;
            traj_parse_start <= 1'b0;
            traj_plan_start  <= 1'b0;
            traj_shape_start <= 1'b0;
            traj_txt_commit  <= 1'b0;
            traj_txt_none    <= 1'b0;
            walk_start       <= 1'b0;
            walk_stop        <= 1'b0;

            // ---- 1. 收集字段 ----
            if (field_valid) begin
                if (field_id == F_HW_ROWS  && field_value != array_rows)     array_bad <= 1'b1;
                if (field_id == F_HW_COLS  && field_value != array_cols)     array_bad <= 1'b1;
                if (field_id == F_HW_PITCH && field_value != array_pitch_um) array_bad <= 1'b1;
                if (field_id == F_MAPPING  && field_enum  != 3'd0)           array_bad <= 1'b1;
                if (field_id == F_VALUE)                                     mode_value <= field_enum[1:0];

                if (verb == V_CONFIG) begin          // CONFIG 的字段先写进影子
                    case (field_id)
                        F_CARRIER_HZ:  sh_carrier_hz     <= field_value;
                        F_PHASE_STEPS: sh_phase_steps    <= field_value;
                        F_CX_UM:       sh_cx_um          <= field_value;
                        F_CY_UM:       sh_cy_um          <= field_value;
                        F_Z_UM:        sh_z_um           <= field_value;
                        F_RADIUS_UM:   sh_radius_um      <= field_value;
                        F_REPEAT:      sh_repeat_millihz <= field_value;
                        F_MOD_HZ:      sh_mod_hz         <= field_value;
                        F_LEVEL:       sh_level          <= field_value;
                        F_SHAPE:       sh_shape          <= field_enum;
                        F_PATH_CLOSED: sh_path_closed    <= field_value;
                        F_PATH_XY: begin
                            sh_path_off  <= field_off;   // 记下位置和长度，坐标串之后再解析
                            sh_path_size <= field_size;
                        end
                        F_SCAN_PATHS: begin
                            sh_scan_off  <= field_off;   // 多段草图同样先只记位置和长度
                            sh_scan_size <= field_size;
                        end
                        F_BLANK_US:    sh_blank_us  <= field_value;
                        default: ;                   // hw_* 与 mapping 只用于核对，不进配置
                    endcase
                end
            end

            // ---- 2. 帧结束：出结论 ----
            // 只认电脑发来的 CMD；其它帧型（理论上不会收到）一律不回话
            if ((frame_ok || frame_bad) && header_ok && kind == 2'd0) begin
                if (pending != P_IDLE) begin
                    // 上一份 CONFIG 还在解析／编译节拍表。电脑正常只会发探活，
                    // 探活照常回；别的命令先回「忙」，这一次编译照旧走完。
                    reply_valid      <= 1'b1;
                    reply_kind       <= (verb == V_PING && dec_err == C_NONE) ? 2'd0 : 2'd1;
                    reply_verb       <= verb;
                    reply_seq        <= seq;
                    reply_code       <= (verb == V_PING && dec_err == C_NONE)
                                        ? C_NONE : C_BUSY;
                    reply_want_state <= 1'b0;
                end else if (dec_err != C_NONE) begin
                    // 结构层或权限层否掉了这一帧：只回一条 ERR，什么都不改
                    reply_valid      <= 1'b1;
                    reply_kind       <= 2'd1;
                    reply_verb       <= verb;
                    reply_seq        <= seq;
                    reply_code       <= dec_err;
                    reply_want_state <= 1'b0;
                end else if (dec_commit && sh_shape == SH_CUSTOM) begin
                    // 这份配置带多段草图：先解析原文，再编译节拍表，
                    // 两步都过了才回 ACK（见 P_PARSE / P_PLAN）
                    pending          <= P_PARSE;
                    traj_parse_start <= 1'b1;
                    traj_preset      <= 1'b0;
                    // 这几拍之后可能要过一毫秒才回话，先把「回给谁」记下来。
                    // 只在这条路上记：编译期间插进来的探活不能把序号顶掉。
                    lat_verb         <= verb;
                    lat_seq          <= seq;
                end else if (dec_commit) begin
                    // 预设图形（CIRCLE / SQUARE / ...）：先按形状生成点表，再编译节拍表。
                    // 同样是都过了才回 ACK。
                    pending          <= P_SHAPE;
                    traj_shape_start <= 1'b1;
                    traj_preset      <= 1'b1;
                    lat_verb         <= verb;
                    lat_seq          <= seq;
                end else begin
                    reply_valid      <= 1'b1;
                    reply_kind       <= 2'd0;
                    reply_verb       <= verb;
                    reply_seq        <= seq;
                    reply_code       <= C_NONE;
                    reply_want_state <= dec_want_state;

                    if (dec_hello)    connected <= 1'b1;
                    if (dec_mode_set) mode      <= dec_mode;
                    if (dec_run) begin
                        run_state <= dec_run_state;
                        reason    <= REASON_NONE;
                        // 走步器跟着运行状态走：停止要归零，从待机启动要回到起点
                        if (dec_run_state == R_IDLE) walk_stop <= 1'b1;
                        else if (dec_run_state == R_RUNNING && run_state == R_IDLE
                                 && traj_ready)
                            // 没有节拍表（比如预设图形）就没什么可走的：状态照旧变
                            // RUNNING，但扫描开关保持 0、输出保持关闭
                            walk_start <= 1'b1;
                    end
                    if (dec_commit) accept_config(1'b0, seq, verb);
                end
            end

            // 一帧用完了，阵列核对标记清零，等下一帧
            if (frame_ok || frame_bad) begin
                array_bad <= 1'b0;
            end

            // ---- 2.5 待办配置：解析原文／生成图形 → 编译节拍表 ----
            case (pending)
                P_PARSE: begin
                    if (traj_parse_bad) begin
                        reject_config(lat_seq, lat_verb);   // 原文不是合法草图
                        pending <= P_IDLE;
                    end else if (traj_parse_ok) begin
                        if (traj_none) begin
                            // 这份配置没有草图（scan_paths=NONE）：不需要节拍表，
                            // 直接生效，轨迹标记清掉
                            accept_config(1'b0, lat_seq, lat_verb);
                            pending <= P_IDLE;
                        end else begin
                            traj_plan_start <= 1'b1;
                            pending         <= P_PLAN;
                        end
                    end
                end

                P_PLAN: begin
                    if (traj_plan_fault) begin
                        reject_config(lat_seq, lat_verb);   // 图形／配置做不出节拍表
                        pending <= P_IDLE;
                    end else if (traj_plan_done) begin
                        accept_config(1'b1, lat_seq, lat_verb);   // 算好了，才真的生效
                        pending <= P_IDLE;
                    end
                end

                P_SHAPE: begin
                    // 预设图形的点表生成完了，接着编译节拍表
                    if (traj_shape_done) begin
                        traj_plan_start <= 1'b1;
                        pending         <= P_PLAN;
                    end
                end

                default: ;
            endcase

            // ---- 3. 心跳超时：REMOTE 下必须关输出回到待机 ----
            if (heartbeat_lost && mode == M_REMOTE) begin
                run_state <= R_IDLE;
                reason    <= REASON_HB;
                walk_stop <= 1'b1;
            end
        end
    end

endmodule
