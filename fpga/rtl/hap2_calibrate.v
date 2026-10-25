`timescale 1ns/1ps

// Fixed receiver, 40 kHz / 64 steps. The host supplies only measured ADC^2
// powers; channel sequencing, relative phase solving, verification and commit
// are performed here. A failed/cancelled run never overwrites saved_offsets.
module hap2_calibrate #(
    parameter integer NULL_SCALED_QC = 0,
    parameter integer CHANNELS = 16,
    parameter integer SOURCE = 1,
    parameter integer REFERENCE = 0,
    parameter integer CLK_HZ = 50000000,
    parameter integer SETTLE_MS = 50,
    parameter integer REPEAT_MS = 220,
    parameter integer TIMEOUT_MS = 6000
) (
    input wire clk, rst_n,
    input wire begin_run, abort_run, clear_saved,
    input wire [7:0] reference,
    input wire import_run,
    input wire [8*CHANNELS-1:0] import_offsets,
    input wire measure_valid, measure_good,
    input wire [31:0] measure_power,
    output reg busy, saved_valid,
    output reg changed, committed,
    output reg [2:0] state, // 0 idle, 1 settling, 2 requesting, 3 solving, 4 done, 5 failed
    output reg [3:0] stage, // 0 noise, 1 reference, 2 single, 3..6 four steps, 7 verify
    output reg [7:0] channel,
    output reg [3:0] error,
    output wire [1:0] repeat_number,
    output reg [31:0] token,
    output wire tx_on,
    output reg [CHANNELS-1:0] channel_mask,
    output reg [8*CHANNELS-1:0] trial_offsets,
    output reg [8*CHANNELS-1:0] saved_offsets
);
    localparam [2:0] IDLE=0, SETTLE=1, REQUEST=2, SOLVE=3, DONE=4, FAILED=5;
    // Millisecond timer: 120 seconds at 50 MHz does not fit in 32 cycles bits.
    localparam integer TIMEOUT_LIMIT = SOURCE==0 ? 120000 : TIMEOUT_MS;
    reg [31:0] timer, settle_limit, ms_div;
    wire ms_tick=ms_div >= CLK_HZ/1000-1;
    reg [9:0] quiet_count;
    reg [7:0] ref_channel;
    reg importing;
    always @(posedge clk) begin
        if(!rst_n || ms_tick) ms_div<=0;
        else ms_div<=ms_div+1;
    end
    reg [1:0] repeat_index;
    reg [23:0] r0, r1, sampled_power;
    reg sample_pending;
    reg [2:0] sample_wait;
    reg [23:0] noise, reference_power, single_power;
    reg [23:0] powers [0:3];
    reg [8*CHANNELS-1:0] candidate;
    reg [5:0] trim, scan_index, best_index;
    reg [2:0] solve_state;
    reg signed [32:0] cosine, sine;
    reg signed [63:0] score, best_score;
    reg [47:0] product;
    reg sqrt_start;
    wire sqrt_done;
    wire [23:0] sqrt_value;
    reg [31:0] expected_peak;

    fix_sqrt #(.IN_BITS(48), .OUT_BITS(24)) u_expected (
        .clk(clk), .rst_n(rst_n), .start(sqrt_start), .value(product),
        .done(sqrt_done), .result(sqrt_value)
    );

    function [31:0] median3;
        input [31:0] a,b,c;
        begin
            median3 = a > b ? (b > c ? b : (a > c ? c : a))
                            : (a > c ? a : (b > c ? c : b));
        end
    endfunction
    function [31:0] max3;
        input [31:0] a,b,c;
        begin max3 = a > b ? (a > c ? a : c) : (b > c ? b : c); end
    endfunction
    function [31:0] min3;
        input [31:0] a,b,c;
        begin min3 = a < b ? (a < c ? a : c) : (b < c ? b : c); end
    endfunction
    // Q14 sine for 64 discrete candidates. A lookup plus a maximum projection
    // gives exactly the nearest supported phase without floating point/atan2.
    function signed [15:0] sin64;
        input [5:0] index;
        reg [4:0] folded;
        reg signed [15:0] magnitude;
        begin
            folded = index[4:0] > 16 ? 32-index[4:0] : index[4:0];
            case (folded)
                0:magnitude=0; 1:magnitude=1606; 2:magnitude=3196;
                3:magnitude=4756; 4:magnitude=6270; 5:magnitude=7723;
                6:magnitude=9102; 7:magnitude=10394; 8:magnitude=11585;
                9:magnitude=12665; 10:magnitude=13623; 11:magnitude=14449;
                12:magnitude=15137; 13:magnitude=15679; 14:magnitude=16069;
                15:magnitude=16305; default:magnitude=16384;
            endcase
            sin64 = index[5] ? -magnitude : magnitude;
        end
    endfunction

    wire [23:0] model_noise = SOURCE==0 ? 24'd0 : noise;
    wire [23:0] a = reference_power > model_noise ? reference_power-model_noise : 0;
    wire [23:0] b = single_power > model_noise ? single_power-model_noise : 0;
    // In destructive interference, relative error to the nearly-zero measured
    // power is ill-conditioned. For quadrature trials ONLY, bound the absolute
    // spread by 15% of (a+b)/16 = 0.9375% of the two single-emitter powers.
    // Bright trials retain their original 15% relative rule; background,
    // singles and corrected-pair verification are unchanged. Phase-fit and
    // post-correction checks still independently reject inconsistent data.
    wire [24:0] pair_power = {1'b0,a}+{1'b0,b};
    wire [31:0] null_floor = {7'd0,pair_power} >> 4;
    reg [31:0] median,spread;
    wire [31:0] repeat_scale = NULL_SCALED_QC && stage>=3 && stage<=6 && null_floor>median
                               ? null_floor : median;
    reg unstable;
    reg [38:0] sample100,verify_low,verify_high,verify_quad;
    reg [23:0] max01,max23,strongest;
    always @(posedge clk) begin
        if(!rst_n) begin
            median<=0;spread<=0;unstable<=0;sample100<=0;verify_low<=0;verify_high<=0;verify_quad<=0;
            max01<=0;max23<=0;strongest<=0;
        end else begin
            median<=median3({8'd0,r0},{8'd0,r1},{8'd0,sampled_power});
            spread<=max3({8'd0,r0},{8'd0,r1},{8'd0,sampled_power})-min3({8'd0,r0},{8'd0,r1},{8'd0,sampled_power});
            unstable<=spread>4 && {7'd0,spread}*100>{7'd0,repeat_scale}*15;
            sample100<=median*100;
            verify_low<=expected_peak*85; verify_high<=expected_peak*120;
            max01<=powers[0]>powers[1] ? powers[0] : powers[1];
            max23<=powers[2]>powers[3] ? powers[2] : powers[3];
            strongest<=max01>max23 ? max01 : max23;
            verify_quad<=strongest*90;
        end
    end
    wire [47:0] ab = a*b;
    wire signed [24:0] c_fit=$signed({1'b0,powers[0]})-$signed({1'b0,powers[2]});
    wire signed [24:0] s_fit=$signed({1'b0,powers[3]})-$signed({1'b0,powers[1]});
    wire signed [25:0] residual=$signed({2'b0,powers[0]})+$signed({2'b0,powers[2]})
                              -$signed({2'b0,powers[1]})-$signed({2'b0,powers[3]});
    // Quality checks are deliberately pipelined: differences -> squares ->
    // scaled values -> comparisons. Calibration is slow, but all logic still
    // has to meet the board's 50 MHz clock (no multi-cycle timing exceptions).
    reg signed [24:0] c_q,s_q;
    reg signed [25:0] residual_q;
    reg [27:0] sum_q,target_q,dc_error_q;
    reg [47:0] ab_q;
    reg [49:0] contrast_q;
    reg [51:0] residual2_q;
    reg [56:0] contrast100_q;
    reg [61:0] contrast_lo_q,contrast_hi_q,residual_limit_q;
    reg [65:0] residual_scaled_q;
    reg fit_bad_q,weak_bad_q;
    reg [2:0] qc_wait;
    always @(posedge clk) begin
        if(!rst_n) begin
            c_q<=0;s_q<=0;residual_q<=0;sum_q<=0;target_q<=0;dc_error_q<=0;ab_q<=0;
            contrast_q<=0;residual2_q<=0;contrast100_q<=0;contrast_lo_q<=0;contrast_hi_q<=0;
            residual_limit_q<=0;residual_scaled_q<=0;fit_bad_q<=1;weak_bad_q<=1;
        end else begin
            c_q<=c_fit;s_q<=s_fit;residual_q<=residual;
            sum_q<={4'd0,powers[0]}+powers[1]+powers[2]+powers[3];
            target_q<=4*({4'd0,a}+b+model_noise);
            ab_q<=ab;
            contrast_q<=c_q*c_q+s_q*s_q;
            residual2_q<=residual_q*residual_q;
            dc_error_q<=sum_q>target_q ? sum_q-target_q : target_q-sum_q;
            contrast100_q<=contrast_q*100;
            contrast_lo_q<=ab_q*784; contrast_hi_q<=ab_q*2704;
            residual_scaled_q<=residual2_q*10000; residual_limit_q<=ab_q*9216;
            fit_bad_q<=dc_error_q>({4'd0,a}+b) || contrast100_q<contrast_lo_q ||
                       contrast100_q>contrast_hi_q || residual_scaled_q>residual_limit_q;
            weak_bad_q<=a<=1 || b<=1 || {8'd0,a}<=25*noise || {8'd0,b}<=25*noise;
        end
    end
    // Quiet startup covers output-table reload (including a 256-channel table).
    assign repeat_number=repeat_index;
    assign tx_on = busy && stage != 0 && (state == REQUEST ||
                   (state == SETTLE && quiet_count==1023));
    always @* begin
        channel_mask = 0;
        trial_offsets = 0;
        if (stage == 1) channel_mask[ref_channel] = 1'b1;
        else if (stage >= 2) begin
            channel_mask[channel] = 1'b1;
            if (stage >= 3) channel_mask[ref_channel] = 1'b1;
            if (stage >= 3 && stage <= 6) trial_offsets[8*channel +: 8] = (stage-3)*16;
            else if (stage == 7) trial_offsets[8*channel +: 8] = {2'b0,trim};
        end
    end

    task fail_run;
        input [3:0] reason;
        begin busy<=0; state<=FAILED; error<=reason; changed<=1; end
    endtask
    task next_condition;
        input [3:0] next_stage;
        begin
            stage<=next_stage; state<=SETTLE; timer<=0;
            settle_limit<=SETTLE_MS; quiet_count<=0; repeat_index<=0; changed<=1;
        end
    endtask

    always @(posedge clk) begin
        if (!rst_n) begin
            ref_channel<=REFERENCE; importing<=0;
            busy<=0; saved_valid<=0; changed<=0; committed<=0;
            state<=IDLE; stage<=0; channel<=0; error<=0; token<=0;
            timer<=0; settle_limit<=SETTLE_MS; quiet_count<=0; repeat_index<=0;
            sample_pending<=0; sample_wait<=0; sampled_power<=0; r0<=0; r1<=0; noise<=0; reference_power<=0; single_power<=0;
            powers[0]<=0; powers[1]<=0; powers[2]<=0; powers[3]<=0;
            candidate<=0; saved_offsets<=0; trim<=0; scan_index<=0;
            best_index<=0; solve_state<=0; cosine<=0; sine<=0;
            qc_wait<=0; score<=0; best_score<=0; product<=0; sqrt_start<=0; expected_peak<=0;
        end else begin
            changed<=0; committed<=0; sqrt_start<=0;
            if (abort_run && busy) fail_run(1);
            else if (clear_saved && !busy) begin
                saved_valid<=0; saved_offsets<=0; state<=IDLE; error<=0;
                changed<=1; committed<=1;
            end else if (begin_run && !busy) begin
                if (token >= 32'h7fffffff-8192 || CHANNELS<2 || reference>=CHANNELS || (import_run && import_offsets[8*reference +: 8]!=0)) fail_run(7);
                else begin
                    busy<=1; candidate<=import_run ? import_offsets : 0; importing<=import_run; error<=0; ref_channel<=reference;
                    channel<=reference==0 ? 1 : 0;
                    next_condition(0);
                end
            end else if (busy) begin
                case (state)
                    SETTLE: begin
                        if(quiet_count!=1023) quiet_count<=quiet_count+1;
                        if (timer >= settle_limit) begin
                            state<=REQUEST; timer<=0; token<=token+1;
                        end else if(ms_tick) timer<=timer+1;
                    end
                    REQUEST: begin
                        if (measure_valid) begin
                            if (!measure_good || measure_power>32'h00ffffff) fail_run(3);
                            else begin
                                sampled_power<=measure_power[23:0]; sample_pending<=1;
                                sample_wait<=0; state<=SOLVE;
                            end
                        end else if (timer>=TIMEOUT_LIMIT) fail_run(2);
                        else if(ms_tick) timer<=timer+1;
                    end
                    SOLVE: begin
                        if(sample_pending) begin
                            if(sample_wait<4) sample_wait<=sample_wait+1;
                            else begin
                                sample_pending<=0;
                            if (repeat_index==0) begin
                                r0<=sampled_power; repeat_index<=1;
                                state<=SETTLE; timer<=0; settle_limit<=REPEAT_MS; quiet_count<=0;
                            end else if (repeat_index==1) begin
                                r1<=sampled_power; repeat_index<=2;
                                state<=SETTLE; timer<=0; settle_limit<=REPEAT_MS; quiet_count<=0;
                            end else if (unstable) fail_run(3);
                            else case (stage)
                                0: begin noise<=median; next_condition(1); end
                                1: begin reference_power<=median; next_condition(2); end
                                2: begin
                                    single_power<=median;
                                    if(importing) begin state<=SOLVE; solve_state<=4; qc_wait<=0; end
                                    else next_condition(3);
                                end
                                3,4,5: begin powers[stage-3]<=median; next_condition(stage+1); end
                                6: begin powers[3]<=median; state<=SOLVE; solve_state<=0; qc_wait<=0; end
                                7: begin
                                    if (sample100 < verify_low ||
                                        sample100 > verify_high ||
                                        (!importing && sample100 < verify_quad)) fail_run(6);
                                    else begin
                                        candidate[8*channel +: 8]<={2'b0,trim};
                                        if (channel==CHANNELS-1 || (channel==CHANNELS-2 && ref_channel==CHANNELS-1)) begin
                                            saved_offsets<=candidate;
                                            saved_offsets[8*channel +: 8]<={2'b0,trim};
                                            saved_valid<=1; committed<=1; busy<=0; state<=DONE; changed<=1;
                                        end else begin
                                            channel<=channel+1==ref_channel ? channel+2 : channel+1;
                                            next_condition(2);
                                        end
                                    end
                                end
                                default: fail_run(5);
                            endcase

                            end
                        end else case (solve_state)
                            0: begin
                                if(qc_wait<6) qc_wait<=qc_wait+1;
                                else if (weak_bad_q) fail_run(4);
                                else if (fit_bad_q) fail_run(5);
                                else begin
                                    cosine<=c_fit; sine<=s_fit; scan_index<=0;
                                    best_score<=64'sh8000000000000000; best_index<=0;
                                    product<=ab[47:0]; sqrt_start<=1; solve_state<=1;
                                end
                            end
                            1: begin
                                score<=cosine*sin64(scan_index+6'd16)-sine*sin64(scan_index);
                                solve_state<=2;
                            end
                            2: begin
                                if (score>best_score) begin best_score<=score; best_index<=scan_index; end
                                if (scan_index==63) solve_state<=3;
                                else begin scan_index<=scan_index+1; solve_state<=1; end
                            end
                            3: begin
                                // The sqrt finishes before the 128-cycle candidate scan.
                                expected_peak<={8'd0,a}+b+2*sqrt_value+model_noise;
                                trim<=best_index; next_condition(7);
                            end
                            4: begin
                                if(qc_wait<3) qc_wait<=qc_wait+1;
                                else if(weak_bad_q) fail_run(4);
                                else begin product<=ab; sqrt_start<=1; solve_state<=5; end
                            end
                            5: if(sqrt_done) begin
                                expected_peak<={8'd0,a}+b+2*sqrt_value+model_noise;
                                trim<=candidate[8*channel +: 6]; next_condition(7);
                            end
                            default: fail_run(5);
                        endcase
                    end
                    default: fail_run(5);
                endcase
            end
        end
    end
endmodule
