`timescale 1ns/1ps

// 主机心跳看门狗：电脑不在了，板子必须自己停下来。
//
// 这是整条链路的最后一道防线。前面所有的防护（校验码、应答、超时断开）
// 都依赖电脑还活着；万一电脑崩溃、程序被杀、或者 USB 线被拔掉，
// 最后能救场的只有板子自己。
//
// 判定规则（协议第 6 节）：
//   - 只在「电脑控制」且「正在运行或暂停」时看守；待机时没什么可停的；
//   - 超过 HB_MS 毫秒没收到主机的合法报文，就给出一个超时脉冲；
//   - 主机一旦又发来合法报文，计时重新开始；
//   - 本地控制模式不适用——板子本来就该独立运行。
//
// 注意：只有「合法」报文才刷新计时。校验码错的报文不算数——垃圾数据
// 不能证明电脑还在。
module hap2_watchdog #(
    parameter integer CLK_HZ = 50_000_000,   // 系统时钟频率（Hz）
    parameter integer HB_MS  = 3000          // 心跳超时（毫秒），与 HELLO 声明一致
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       host_frame,   // 单拍脉冲：收到一条合法的主机报文
    input  wire [1:0] mode,         // 1 = REMOTE
    input  wire [1:0] run_state,    // 1 = RUNNING，2 = PAUSED
    output reg        timeout       // 单拍脉冲：主机失联
);

    localparam integer CLKS_PER_MS = (CLK_HZ + 500) / 1000;

    localparam [1:0] M_REMOTE  = 2'd1;
    localparam [1:0] R_RUNNING = 2'd1;
    localparam [1:0] R_PAUSED  = 2'd2;

    wire armed = (mode == M_REMOTE) && (run_state == R_RUNNING || run_state == R_PAUSED);

    reg [31:0] tick;      // 毫秒内的时钟计数
    reg [31:0] ms;        // 距上次主机报文过了多少毫秒
    reg        expired;   // 已经数到超时，等下一次主机报文才重新开始
    reg        fired;     // 这次超时已经报过了，不要重复报

    always @(posedge clk) begin
        if (!rst_n) begin
            tick    <= 32'd0;
            ms      <= 32'd0;
            expired <= 1'b0;
            fired   <= 1'b0;
            timeout <= 1'b0;
        end else begin
            timeout <= 1'b0;

            if (host_frame) begin
                // 主机还在说话，计时重来
                tick    <= 32'd0;
                ms      <= 32'd0;
                expired <= 1'b0;
                fired   <= 1'b0;
            end else if (!expired) begin
                if (tick >= CLKS_PER_MS - 1) begin
                    tick <= 32'd0;
                    if (ms + 1'b1 >= HB_MS) begin
                        ms      <= HB_MS;
                        expired <= 1'b1;
                    end else begin
                        ms <= ms + 1'b1;
                    end
                end else begin
                    tick <= tick + 1'b1;
                end
            end

            // 数到超时、而且此刻确实在跑（或暂停），报一次
            if (expired && armed && !fired) begin
                timeout <= 1'b1;
                fired   <= 1'b1;
            end
        end
    end

endmodule
