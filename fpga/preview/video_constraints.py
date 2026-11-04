"""Tang Pro DVI0 pads and local serializer reset register placement."""


def append_video_constraints(cst):
    for name, pair in [('tmds_data_p[0]', 'AB24,AC24'),
                       ('tmds_data_p[1]', 'V24,W24'),
                       ('tmds_data_p[2]', 'AA22,AA23'),
                       ('tmds_clk_p', 'Y22,Y23')]:
        cst += f'\nIO_LOC "{name}" {pair};\nIO_PORT "{name}" PULL_MODE=NONE DRIVE=3.5;\n'
    # Each reset FF sits next to its right-edge I/O serializer. Without this
    # placement a small design can put all four FFs in the middle of the die,
    # exceeding the half-fast-clock recovery budget. Keep recovery/removal timed.
    for lane, row in enumerate((35, 47, 33, 29)):
        cst += f'INS_LOC "reset_driver/lane[{lane}].reset_ff" R{row}C180[0][A];\n'
    return cst
