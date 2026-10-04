`timescale 1ns/1ps

// 预设图形的轨迹：把 CIRCLE / SQUARE / TRIANGLE / LINE_X / LINE_Y / POINT / ARROW
// 这些「用参数描述的图形」变成一张点表，交给轨迹节拍表模块去编译。
//
// 参考实现是 desktop_app/model.py 的 trajectory_point()，口径逐条对齐：
//
//   POINT     一个点（图形中心），整圈停在原地
//   LINE_X    沿 x 来回：位置 = 1 − 4|f − 0.5|，也就是 −1 → +1 → −1
//   LINE_Y    同理，沿 y
//   CIRCLE    半径 r 的整圆
//   SQUARE    边长 2r 的正方形（四个角，末尾回到起点）
//   TRIANGLE  顶点 (0,1)、(−0.866,−0.5)、(0.866,−0.5) 的三角形
//   ARROW     (−1,0)→(1,0)→(0.3,0.7)→(1,0)→(0.3,−0.7)→(1,0)→(−1,0)
//
// 两条和草图不同的口径，别搞混：
//   1. **没有抬笔时间**：整圈都在扫描，scan_on 恒为 1（参考实现就是这么写的）。
//   2. 折线按**路径长度**参数化（和参考实现的 _polyline 一致），所以同样的
//      「匀速度」是自动的。
//
// 坐标：图形坐标以中心为准，顶点用 Q16 定点（单位向量 × 65536），乘半径后右移 16 位；
// 圆没有折线顶点表，是「单位向量每次转 Δ = 2π/128」转出来的（cos/sin 用 Q24 常数，
// 128 边形对 20 mm 半径的误差上限 6 µm，远小于一个相位档）。
module hap2_shape #(
    parameter integer PT_BITS  = 21,
    parameter integer CIRCLE_N = 128      // 圆用多少条边逼近（顶点数 = N+1）
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire                      start,        // 单拍：开始生成
    input  wire [2:0]                shape,        // 0 POINT 1 LINE_X 2 LINE_Y 3 CIRCLE 4 SQUARE 5 TRIANGLE 6 ARROW
    /* verilator lint_off UNUSEDSIGNAL */
    // 这三个字段的取值范围由字段解析层把关（半径 ≤ 80000、中心 ±100000），
    // 折半换算成 0.5 µm 单位后高位用不到
    input  wire [31:0]               radius_um,    // 半径／半长（微米）
    input  wire [31:0]               cx_um,        // 图形中心（微米）
    input  wire [31:0]               cy_um,
    /* verilator lint_on UNUSEDSIGNAL */
    // ---- 往点表写（每拍一个点）----
    output reg                       pt_wr_en,
    output reg  [7:0]                pt_wr_addr,
    output reg  signed [PT_BITS-1:0] pt_wr_x,
    output reg  signed [PT_BITS-1:0] pt_wr_y,
    // ---- 段表与计数（生成完一次性写）----
    output reg                       st_wr_en,
    output reg  [4:0]                st_wr_addr,
    output reg  [8:0]                st_wr_start,
    output reg  [8:0]                st_wr_len,
    output reg                       counts_en,
    output reg  [8:0]                npt_out,
    output reg  [5:0]                nst_out,
    output reg                       busy,
    output reg                       done
);

    // 顶点数（每种的顶点个数和参考实现里的折线顶点表一一对应）
    wire [7:0] nvert = (shape == 3'd0) ? 8'd1                       // POINT
                     : (shape == 3'd1) ? 8'd3                       // LINE_X
                     : (shape == 3'd2) ? 8'd3                       // LINE_Y
                     : (shape == 3'd3) ? (CIRCLE_N[7:0] + 8'd1)     // CIRCLE
                     : (shape == 3'd4) ? 8'd5                       // SQUARE
                     : (shape == 3'd5) ? 8'd4                       // TRIANGLE
                     :                   8'd7;                      // ARROW

    reg  [7:0]        vtx;          // 正在算第几个顶点
    reg signed [20:0] r_units;      // 半径（0.5 µm 单位）
    reg signed [20:0] cx_q, cy_q;   // 图形中心（0.5 µm 单位）
    reg signed [17:0] ux_rot, uy_rot;   // 圆的单位向量（Q16），从 (1,0) 开始转

    // ---------------- 单位向量表（Q16）----------------
    // 只有折线图形用得到；圆用 ux_rot / uy_rot。
    reg signed [17:0] ux_q, uy_q;
    always @* begin
        ux_q = 18'sd0;
        uy_q = 18'sd0;
        case (shape)
            3'd0: ;                                             // POINT：(0,0)
            3'd1: ux_q = (vtx == 8'd1) ? 18'sd65536 : -18'sd65536;   // LINE_X
            3'd2: uy_q = (vtx == 8'd1) ? 18'sd65536 : -18'sd65536;   // LINE_Y
            3'd3: begin ux_q = ux_rot; uy_q = uy_rot; end       // CIRCLE
            3'd4: begin                                         // SQUARE
                case (vtx)
                    8'd0: begin ux_q = -18'sd65536; uy_q = -18'sd65536; end
                    8'd1: begin ux_q =  18'sd65536; uy_q = -18'sd65536; end
                    8'd2: begin ux_q =  18'sd65536; uy_q =  18'sd65536; end
                    8'd3: begin ux_q = -18'sd65536; uy_q =  18'sd65536; end
                    default: begin ux_q = -18'sd65536; uy_q = -18'sd65536; end
                endcase
            end
            3'd5: begin                                         // TRIANGLE
                case (vtx)
                    8'd0: begin ux_q =  18'sd0;     uy_q =  18'sd65536; end
                    8'd1: begin ux_q = -18'sd56754; uy_q = -18'sd32768; end
                    8'd2: begin ux_q =  18'sd56754; uy_q = -18'sd32768; end
                    default: begin ux_q = 18'sd0;   uy_q =  18'sd65536; end
                endcase
            end
            default: begin                                      // ARROW
                case (vtx)
                    8'd0: begin ux_q = -18'sd65536; uy_q =  18'sd0; end
                    8'd1: begin ux_q =  18'sd65536; uy_q =  18'sd0; end
                    8'd2: begin ux_q =  18'sd19661; uy_q =  18'sd45875; end
                    8'd3: begin ux_q =  18'sd65536; uy_q =  18'sd0; end
                    8'd4: begin ux_q =  18'sd19661; uy_q = -18'sd45875; end
                    8'd5: begin ux_q =  18'sd65536; uy_q =  18'sd0; end
                    default: begin ux_q = -18'sd65536; uy_q = 18'sd0; end
                endcase
            end
        endcase
    end

    // 顶点 = 中心 + （单位向量 × 半径）四舍五入
    // r_units 也声明成有符号，乘法两边才都是 signed，负数才不会算错
    wire signed [39:0] vx_prod = (ux_q * r_units) + 40'sd32768;
    wire signed [39:0] vy_prod = (uy_q * r_units) + 40'sd32768;

    // 圆的单位向量转一步：Q16 × Q24 → 右移 24 位回到 Q16（加 2^23 四舍五入）
    localparam signed [24:0] COS_Q24 = 25'sd16757007;     // cos(2π/128)
    localparam signed [24:0] SIN_Q24 = 25'sd823219;       // sin(2π/128)
    wire signed [43:0] rot_x = (ux_rot * COS_Q24) - (uy_rot * SIN_Q24) + 44'sd8388608;
    wire signed [43:0] rot_y = (ux_rot * SIN_Q24) + (uy_rot * COS_Q24) + 44'sd8388608;

    localparam [1:0] S_IDLE = 2'd0;
    localparam [1:0] S_VERT = 2'd1;
    localparam [1:0] S_ST   = 2'd2;
    localparam [1:0] S_DONE = 2'd3;

    reg [1:0] state;

    always @(posedge clk) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            busy       <= 1'b0;
            done       <= 1'b0;
            vtx        <= 8'd0;
            r_units    <= 21'sd0;
            cx_q       <= 21'sd0;
            cy_q       <= 21'sd0;
            ux_rot     <= 18'sd65536;
            uy_rot     <= 18'sd0;
            pt_wr_en   <= 1'b0;
            pt_wr_addr <= 8'd0;
            pt_wr_x    <= {PT_BITS{1'b0}};
            pt_wr_y    <= {PT_BITS{1'b0}};
            st_wr_en   <= 1'b0;
            st_wr_addr <= 5'd0;
            st_wr_start<= 9'd0;
            st_wr_len  <= 9'd0;
            counts_en  <= 1'b0;
            npt_out    <= 9'd0;
            nst_out    <= 6'd0;
        end else begin
            done     <= 1'b0;
            pt_wr_en <= 1'b0;
            st_wr_en <= 1'b0;
            counts_en<= 1'b0;

            case (state)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (start) begin
                        busy    <= 1'b1;
                        vtx     <= 8'd0;
                        r_units <= $signed({11'h0, radius_um[19:0]}) * 2;   // 微米 → 0.5 µm
                        cx_q    <= $signed({11'h0, cx_um[20:0]})   * 2;
                        cy_q    <= $signed({11'h0, cy_um[20:0]})   * 2;
                        ux_rot  <= 18'sd65536;                    // 圆的单位向量从 (1,0) 开始
                        uy_rot  <= 18'sd0;
                        state   <= S_VERT;
                    end
                end

                // 每拍算一个顶点写进点表，顺手把圆的单位向量转一步（其它图形转了也不用）
                S_VERT: begin
                    pt_wr_en   <= 1'b1;
                    pt_wr_addr <= vtx;
                    pt_wr_x    <= cx_q + (vx_prod >>> 16);
                    pt_wr_y    <= cy_q + (vy_prod >>> 16);
                    ux_rot     <= rot_x >>> 24;
                    uy_rot     <= rot_y >>> 24;
                    if (vtx + 8'd1 >= nvert) state <= S_ST;
                    else                     vtx   <= vtx + 8'd1;
                end

                // 一整条折线就是一笔
                S_ST: begin
                    st_wr_en    <= 1'b1;
                    st_wr_addr  <= 5'd0;
                    st_wr_start <= 9'd0;
                    st_wr_len   <= {1'b0, nvert};
                    counts_en   <= 1'b1;
                    npt_out     <= {1'b0, nvert};
                    nst_out     <= 6'd1;
                    state       <= S_DONE;
                end

                default: begin      // S_DONE
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= S_IDLE;
                end
            endcase
        end
    end

endmodule
