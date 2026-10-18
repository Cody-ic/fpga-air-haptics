`timescale 1ns/1ps

// 逐通道校准寄存器与文本解析器。
// offsets 是逗号分隔的十进制相位码；geometry_id 必须与当前阵列完全一致。
// 所有字符都校验完以后才一次性发布，坏命令不会留下半张表。
module hap2_calibration #(
    parameter integer CHANNELS = 16,
    parameter integer ADDR_W = 13,
    parameter [8*24-1:0] GEOMETRY_ID = "d3e4d118b6b2ca2469907382"
) (
    input  wire clk,
    input  wire rst_n,
    input  wire start,
    input  wire action_store,
    input  wire [31:0] requested_mask,
    input  wire [31:0] phase_steps,
    input  wire [ADDR_W:0] geometry_off,
    input  wire [ADDR_W:0] geometry_len,
    input  wire [ADDR_W:0] offsets_off,
    input  wire [ADDR_W:0] offsets_len,
    output reg  [ADDR_W-1:0] rd_addr,
    input  wire [7:0] rd_data,
    output reg busy,
    output reg done,
    output reg valid,
    output reg changed,
    input  wire restore,
    output reg trial_active,
    output reg [CHANNELS-1:0] channel_mask,
    output reg [CHANNELS*8-1:0] phase_offsets
);
    localparam [2:0] S_IDLE=0, S_GPRIME=1, S_GEOM=2, S_OPRIME=3,
                     S_OFF=4, S_COMMIT=5;
    localparam [CHANNELS-1:0] ALL_ON = {CHANNELS{1'b1}};
    localparam [31:0] MASK_LIMIT = (CHANNELS == 32) ? 32'hffffffff
                                                    : ((32'h1 << CHANNELS) - 1'b1);

    reg [2:0] state;
    reg wait_data;
    reg [ADDR_W:0] pos;
    reg [8:0] channel;
    reg [8:0] value;
    reg [3:0] digits;
    reg bad;
    reg store_q;
    reg [CHANNELS-1:0] mask_q;
    reg [CHANNELS*8-1:0] candidate;
    reg [CHANNELS*8-1:0] saved_offsets;

    wire geom_last = (pos + 1'b1 >= geometry_len);
    wire off_last  = (pos + 1'b1 >= offsets_len);
    wire is_digit = (rd_data >= "0") && (rd_data <= "9");
    wire [8:0] next_value = value * 10 + rd_data[3:0];
    wire [7:0] expected_geom = GEOMETRY_ID[8*24-1 - 8*pos -: 8];

    always @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE; busy <= 1'b0; done <= 1'b0; valid <= 1'b0; changed <= 1'b0;
            rd_addr <= 0; wait_data <= 1'b0; pos <= 0; channel <= 0;
            value <= 0; digits <= 0; bad <= 1'b0; store_q <= 1'b0;
            mask_q <= ALL_ON; candidate <= 0; saved_offsets <= 0;
            phase_offsets <= 0; channel_mask <= ALL_ON; trial_active <= 1'b0;
        end else begin
            done <= 1'b0;
            changed <= 1'b0;
            if (restore) begin
                phase_offsets <= saved_offsets;
                channel_mask <= ALL_ON;
                trial_active <= 1'b0;
                changed <= 1'b1;
            end
            case (state)
                S_IDLE: if (start) begin
                    busy <= 1'b1; valid <= 1'b0; bad <= 1'b0;
                    store_q <= action_store;
                    mask_q <= requested_mask[CHANNELS-1:0];
                    candidate <= 0; pos <= 0; channel <= 0; value <= 0; digits <= 0;
                    rd_addr <= geometry_off[ADDR_W-1:0];
                    if (geometry_len != 24 || offsets_len == 0
                        || (requested_mask[CHANNELS-1:0] == 0)
                        || |(requested_mask & ~MASK_LIMIT)
                        || (action_store && requested_mask[CHANNELS-1:0] != ALL_ON)) begin
                        bad <= 1'b1; state <= S_COMMIT;
                    end else state <= S_GPRIME;
                end
                S_GPRIME: begin wait_data <= 1'b0; state <= S_GEOM; end
                S_GEOM: begin
                    if (!wait_data) wait_data <= 1'b1;
                    else begin
                        wait_data <= 1'b0;
                        if (rd_data != expected_geom) bad <= 1'b1;
                        if (geom_last) begin
                            pos <= 0; rd_addr <= offsets_off[ADDR_W-1:0];
                            state <= S_OPRIME;
                        end else begin
                            pos <= pos + 1'b1; rd_addr <= rd_addr + 1'b1;
                        end
                    end
                end
                S_OPRIME: begin wait_data <= 1'b0; state <= S_OFF; end
                S_OFF: begin
                    if (!wait_data) wait_data <= 1'b1;
                    else begin
                        wait_data <= 1'b0;
                        if (is_digit) begin
                            if (digits >= 3 || next_value >= phase_steps[8:0]) bad <= 1'b1;
                            value <= next_value; digits <= digits + 1'b1;
                            if (off_last) begin
                                if (channel != CHANNELS-1) bad <= 1'b1;
                                candidate[channel*8 +: 8] <= next_value[7:0];
                                state <= S_COMMIT;
                            end
                        end else if (rd_data == ",") begin
                            if (digits == 0 || channel >= CHANNELS-1) bad <= 1'b1;
                            candidate[channel*8 +: 8] <= value[7:0];
                            channel <= channel + 1'b1; value <= 0; digits <= 0;
                            if (off_last) begin bad <= 1'b1; state <= S_COMMIT; end
                        end else begin
                            bad <= 1'b1;
                            if (off_last) state <= S_COMMIT;
                        end
                        if (!off_last) begin pos <= pos + 1'b1; rd_addr <= rd_addr + 1'b1; end
                    end
                end
                S_COMMIT: begin
                    busy <= 1'b0; done <= 1'b1; valid <= !bad;
                    if (!bad) begin
                        phase_offsets <= candidate;
                        channel_mask <= mask_q;
                        trial_active <= !store_q;
                        if (store_q) saved_offsets <= candidate;
                        changed <= 1'b1;
                    end
                    state <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
