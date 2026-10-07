`timescale 1ns/1ps

// 输出级：把「每一路的相位码」变成 16 路 40 kHz 方波，送给驱动电路。
//
// 相位码的含义是「这一路比基准提前多少个相位档」。做法是最常见的数字波形生成法：
//
//   相位累加器  acc   每个时钟加一个固定量 inc，加满一圈就是一个载波周期
//   第 i 路     相位 = acc + 码_i × (一圈 / S)，取最高位就是方波（自动 50% 占空比）
//
// inc 在配置阶段算一次：inc = round(f × 2^N ÷ 时钟频率)。用累加器而不是「数到 1250
// 归零」，是因为 50 MHz ÷ 40 kHz = 1250 拍、而 1250 ÷ 64 档 = 19.53 拍不是整数——
// 硬数会一档一档地抖，累加器则把误差均匀摊开（40 kHz 时实测频率误差 0.002%）。
//
// 三个门控条件（运行中、等级>0、正在扫描）由 enable 一句话带进来；enable=0 时
// 所有输出一律拉低——换能器在跳转、暂停、停止、上电复位时都不能被驱动。
//
// 两个「强弱」旋钮（都是 HAP3 配置里的字段）：
//   level（0~100）   归一化驱动等级 → 用占空比调幅度（level=100 = 满驱动 = 50% 方波）
//   mod_hz（0~1000） 包络调制频率  → 用 mod_hz 的方波把输出整段开/关（0 = 不调制）
//   两者都是**首版约定**：真实换能器的「等级—声压」曲线要用示波器和麦克风标定一次。
//
// 如果驱动板要的是「互补的两个输入」（半桥），把 DEAD_CYC 设成死区拍数，
// out_neg 就是带死区的互补输出（两边同时导通会烧管子，所以要有都不导通的空档）。
// 只用单端驱动时 DEAD_CYC=0、只用 out_pos 就行。
module hap2_out #(
    parameter integer ROWS     = 4,
    parameter integer COLS     = 4,
    parameter integer CLK_HZ   = 50_000_000,
    parameter integer ACC_BITS = 24,     // 相位累加器位宽：越大频率越准
    parameter integer DEAD_CYC = 0       // 互补输出的死区（时钟数），0 = 不插死区
) (
    input  wire                      clk,
    input  wire                      rst_n,
    // ---- 配置 ----
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [31:0]               cfg_carrier_hz,   // 取值范围由字段解析层把关（≤80000）
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire [31:0]               cfg_phase_steps,
    input  wire                      cfg_change,   // 单拍：载波换了，增量重算
    // ---- 调制与等级（HAP3 的 mod_hz / level）----
    // level：0~100 的归一化驱动等级；mod_hz：0~1000 的包络调制频率（0 = 不调制）。
    // 两个都是**首版约定**，真实换能器上的曲线要上板标定，见下面注释。
    input  wire [31:0]               cfg_level,
    input  wire [31:0]               cfg_mod_hz,
    // ---- 门控 ----
    input  wire                      enable,       // 运行中 && 等级>0 && 正在扫描
    // ---- 相位表读口（新表好了就读一遍，锁进本地寄存器）----
    input  wire                      tbl_new,      // 单拍：相位表刚更新
    output reg  [7:0]                tbl_addr,
    input  wire [7:0]                tbl_data,
    // ---- 输出 ----
    output reg  [ROWS*COLS-1:0]      out_pos,
    output reg  [ROWS*COLS-1:0]      out_neg
);

    localparam integer CHANNELS = ROWS * COLS;
    localparam integer DEAD_MAX = (DEAD_CYC < 1) ? 1 : DEAD_CYC;

    // ---------------- 配置阶段：算累加增量 ----------------
    // inc = round(f × 2^ACC_BITS ÷ 时钟频率)
    reg  [55:0] in_numer;
    reg         in_start;
    wire        in_done, in_div0;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [55:0] in_quot;    // 只用到低 ACC_BITS 位
    /* verilator lint_on UNUSEDSIGNAL */

    fix_div #(.NUM_BITS(56), .DEN_BITS(32)) u_inc (
        .clk (clk), .rst_n (rst_n), .start (in_start),
        .numer (in_numer), .denom (CLK_HZ[31:0]),
        .done (in_done), .div0 (in_div0), .quot (in_quot)
    );

    reg [ACC_BITS-1:0] inc;
    reg [5:0]          off_shift;    // ACC_BITS − log2(S)

    // ---------------- 等级：0~100 的百分比 → 占空比门槛 ----------------
    // 参考实现（model.py）只把 level 当「开/关」；协议里 level 是「归一化驱动等级」。
    // 这里定成：level=100 就是原来的 50% 占空比方波（满驱动）；level 越小，
    // 载波周期里高电平那一段越短。方波的基波幅度 ∝ sin(π × 占空比)，所以
    // 占空比从 0 走到 50% 正好是一条从「关」到「满」的单调曲线。这就是数字电路里
    // 常用的「用占空比调幅度」（PWM，Pulse Width Modulation，脉冲宽度调制）。
    // 门槛 ≈ 2^32 ÷ 200 × level，所以 level=100 时门槛 ≈ 半个周期。
    // 首版约定：真实换能器的「等级—声压」曲线要在示波器和麦克风上标定一次。
    // level 的取值范围 0~100 由字段解析层把关；这里再夹一次，万一真收到超范围的值
    // 也不会因为乘法溢出而算出一个奇怪的门槛。
    wire [31:0] duty_thr = (cfg_level > 32'd100) ? 32'd2_147_483_648
                                                 : cfg_level * 32'd21_474_836;

    // ---------------- 调制包络：mod_hz 的 50% 方波 ----------------
    // mod_hz=0 时包络恒为 1（等于不调制）。mod_hz>0 时用一个专门的相位累加器
    // 产生 mod_hz 的方波，把输出整段整段地开/关（触感强弱随时间变化就靠它）。
    // 累加器位宽 40 位：mod_hz=1 时增量也有两万多，不会退化成 0。
    localparam integer      MOD_ACC_BITS = 40;
    reg  [MOD_ACC_BITS-1:0] mod_acc;
    reg  [MOD_ACC_BITS-1:0] mod_inc;
    wire mod_on = (cfg_mod_hz == 32'd0) ? 1'b1 : mod_acc[MOD_ACC_BITS-1];

    function [3:0] log2s;            // S 只可能是 8..256
        input [31:0] s;
        begin
            case (s)
                32'd8:   log2s = 4'd3;
                32'd16:  log2s = 4'd4;
                32'd32:  log2s = 4'd5;
                32'd64:  log2s = 4'd6;
                32'd128: log2s = 4'd7;
                default: log2s = 4'd8;      // 256
            endcase
        end
    endfunction

    // ---------------- 本地相位码与偏移 ----------------
    /* verilator lint_off UNUSEDSIGNAL */
    reg [7:0]          code [0:CHANNELS-1];   // 锁进来的相位码，只为了看波形
    /* verilator lint_on UNUSEDSIGNAL */
    reg [ACC_BITS-1:0] off  [0:CHANNELS-1];
    reg [ACC_BITS-1:0] acc;
    reg [7:0]          load_i;
    reg                loading, load_wait;

    // 读表：给地址后一拍出数据，所以每个通道两拍
    always @(posedge clk) begin
        if (!rst_n) begin
            load_i     <= 8'd0;
            loading    <= 1'b0;
            load_wait  <= 1'b0;
            tbl_addr   <= 8'd0;
        end else begin
            if (tbl_new) begin
                loading   <= 1'b1;
                load_i    <= 8'd0;
                load_wait <= 1'b0;
                tbl_addr  <= 8'd0;
            end else if (loading) begin
                if (load_wait) begin
                    // 数据这一拍有效：锁进本地，偏移也跟着算好
                    code[load_i] <= tbl_data;
                    off [load_i] <= tbl_data << off_shift;
                    load_wait    <= 1'b0;
                    if (load_i + 8'd1 >= CHANNELS[7:0]) begin
                        loading <= 1'b0;
                    end else begin
                        load_i   <= load_i + 8'd1;
                        tbl_addr <= load_i + 8'd1;
                    end
                end else begin
                    load_wait <= 1'b1;
                end
            end
        end
    end

    // ---------------- 相位累加器 ----------------
    always @(posedge clk) begin
        if (!rst_n)          acc <= {ACC_BITS{1'b0}};
        else if (in_done)    acc <= {ACC_BITS{1'b0}};   // 换了载波：从零相位重新开始
        else                 acc <= acc + inc;
    end

    // ---------------- 调制包络累加器 ----------------
    always @(posedge clk) begin
        if (!rst_n)         mod_acc <= {MOD_ACC_BITS{1'b0}};
        else if (cfg_change) mod_acc <= {MOD_ACC_BITS{1'b0}};   // 配置一换，包络从零相位重新开始
        else                mod_acc <= mod_acc + mod_inc;
    end

    // ---------------- 每一路：加相位偏移，和占空比门槛比 ----------------
    genvar gi;
    generate
        for (gi = 0; gi < CHANNELS; gi = gi + 1) begin : g_ch
            wire [ACC_BITS-1:0] ph = acc + off[gi];
            // level=100 时 duty_thr ≈ 半个周期，这一行就等于原来的「取最高位」，
            // 也就是 50% 占空比；等级调低，高电平那一段就变短。
            wire                sq = (ph < duty_thr);
            /* verilator lint_off UNUSEDSIGNAL */
            reg  [DEAD_MAX-1:0] dly;    // 死区延迟链（不用互补输出时也留着，方便看波形）
            /* verilator lint_on UNUSEDSIGNAL */
            // 死区：两个输出各自把**上升沿**推迟 DEAD_CYC 拍，下降沿立即跟随。
            //   out_pos 要 sq 连续为高够久才拉高；out_neg 要 sq 连续为低够久才拉高。
            // 这样每次换向都留出一段「两个都不导通」的空档，而不是两个一起导通。
            // DEAD_CYC=0 时直通，就是严格的互补输出。
            wire sq_pos = (DEAD_CYC == 0) ? sq        : (sq  & (&dly));
            wire sq_neg = (DEAD_CYC == 0) ? (~sq)     : (~sq & (~|dly));

            always @(posedge clk) begin
                if (!rst_n) begin
                    out_pos[gi] <= 1'b0;
                    out_neg[gi] <= 1'b0;
                    dly         <= {(DEAD_MAX){1'b0}};
                end else begin
                    dly         <= (dly << 1) | sq;   // 移位插入，位宽无关
                    // out_gate = 三个门控条件 × 调制包络：任何一个不满足，两个输出一起拉低
                    out_pos[gi] <= out_gate & sq_pos;
                    // enable=0 时两个输出一起拉低（半桥上下管都关，最安全）
                    out_neg[gi] <= out_gate & sq_neg;
                end
            end
        end
    endgenerate

    // ---------------- 状态机（只负责算两个增量：载波 inc、调制 mod_inc）----------------
    /* verilator lint_off UNUSEDSIGNAL */
    reg busy;   // 只在波形上看：算增量的时候是 1
    /* verilator lint_on UNUSEDSIGNAL */

    wire out_gate = enable & mod_on;     // 最终门控：运行为前提，再叠上调制包络

    localparam [1:0] O_IDLE = 2'd0;
    localparam [1:0] O_INC  = 2'd1;
    localparam [1:0] O_MOD  = 2'd2;

    reg [1:0] ostate;

    always @(posedge clk) begin
        if (!rst_n) begin
            busy      <= 1'b0;
            in_start  <= 1'b0;
            in_numer  <= 56'd0;
            inc       <= {ACC_BITS{1'b0}};
            mod_inc   <= {MOD_ACC_BITS{1'b0}};
            off_shift <= ACC_BITS[5:0] - 6'd6;
            ostate    <= O_IDLE;
        end else begin
            in_start <= 1'b0;
            case (ostate)
                O_IDLE: begin
                    if (cfg_change) begin
                        busy      <= 1'b1;
                        in_numer  <= ({{38{1'b0}}, cfg_carrier_hz[17:0]} << ACC_BITS)
                                    + {30'h0, CLK_HZ[25:0]};
                        in_start  <= 1'b1;
                        off_shift <= ACC_BITS[5:0] - {2'b0, log2s(cfg_phase_steps)};
                        ostate    <= O_INC;
                    end
                end
                O_INC: begin
                    if (in_done) begin
                        inc <= in_div0 ? {ACC_BITS{1'b0}} : in_quot[ACC_BITS-1:0];
                        if (cfg_mod_hz == 32'd0) begin
                            busy   <= 1'b0;
                            ostate <= O_IDLE;
                        end else begin
                            // 第二趟：把 mod_hz 也算成「每拍加多少」，同一个除法器接着用
                            in_numer <= ({{46{1'b0}}, cfg_mod_hz[9:0]} << MOD_ACC_BITS)
                                        + {30'h0, CLK_HZ[25:0]};
                            in_start <= 1'b1;
                            ostate   <= O_MOD;
                        end
                    end
                end
                default: begin   // O_MOD
                    if (in_done) begin
                        mod_inc <= in_div0 ? {MOD_ACC_BITS{1'b0}} : in_quot[MOD_ACC_BITS-1:0];
                        busy    <= 1'b0;
                        ostate  <= O_IDLE;
                    end
                end
            endcase
        end
    end

endmodule
