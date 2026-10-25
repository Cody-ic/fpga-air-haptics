"""Single-channel acoustic screening through CAL2; never commits or releases.

Select each emitter as the reference, collect stage 0 background and stage 1
single-emitter captures, then ABORT before submitting the final stage 1 sample.
No synthetic powers, phase solving, uploaded offsets, or skipped safety checks.
"""
import argparse
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics

from calibrate_fpga import (ABORT, MEASURE, READY, SETUP, SETUP_ACK, STATUS,
                            FpgaLink, Settings, checked_receive, heartbeat,
                            measured_power, wait_reply)
from receiver_client import ProtocolError, ReceiverClient, SerialConnection


def summarize(samples):
    groups = {stage: [s for s in samples if s['stage'] == stage] for stage in (0, 1)}
    issues = []
    for stage, group in groups.items():
        if len(group) != 3:
            issues.append('incomplete_background' if stage == 0 else 'incomplete_signal')
        if any(not s['good'] for s in group):
            issues.append('invalid_background' if stage == 0 else 'invalid_signal')
        if len(group) == 3:
            powers = [s['power_adc2'] for s in group]
            median = statistics.median(powers)
            spread = max(powers) - min(powers)
            # Same integer power-domain repeatability rule as hap2_calibrate.
            if spread > 4 and spread * 100 > median * 15:
                issues.append('unstable_background' if stage == 0 else 'unstable_signal')
    result = dict(issues=issues, background_mv=None, signal_mv=None,
                  background_power_adc2=None, signal_power_adc2=None)
    for stage, key in ((0, 'background'), (1, 'signal')):
        if len(groups[stage]) == 3:
            result[key + '_mv'] = statistics.median(s['capture']['peak40_mv'] for s in groups[stage])
            result[key + '_power_adc2'] = statistics.median(s['power_adc2'] for s in groups[stage])
    if all(len(groups[s]) == 3 for s in (0, 1)) and not issues:
        noise = result['background_power_adc2']
        signal = max(0, result['signal_power_adc2'] - noise)
        if signal <= 1 or signal <= 25 * noise:
            issues.append('weak_signal')
    result['screen_pass'] = not issues
    result['phase_verified'] = False
    return result


async def stop_and_confirm(link, boot):
    await link.send(ABORT, boot)
    async with asyncio.timeout(4):
        while True:
            packet = await checked_receive(link, boot)
            if packet.kind == STATUS:
                state = packet.status()
                if not state['busy'] and not state['output']:
                    if state['valid'] or state['state'] == 4:
                        raise ProtocolError('Screening must never commit a calibration')
                    return state


async def screen_one(link, receiver, boot, target, settings, record, beat):
    samples = []
    setup = (*settings.position_um, settings.level, target)
    setup = tuple(v & 0xffffffff for v in setup)
    await link.send(SETUP, boot, 0, *setup)
    await wait_reply(link, boot, SETUP_ACK, lambda p: p.args == setup)
    async with asyncio.timeout(4):
        while True:
            packet = await checked_receive(link, boot)
            if packet.kind == STATUS:
                state = packet.status()
                if state['configured'] and state['reference'] == target:
                    if state['busy'] or state['output'] or state['source'] != 1:
                        raise ProtocolError('Unexpected configured state')
                    break
    last_token = packet.token
    await link.send(READY, boot, 0, 1, 0)
    started = False
    failure = None
    try:
        async with asyncio.timeout(25):
            while True:
                packet = await checked_receive(link, boot)
                if beat.done():
                    beat.result()
                if packet.kind != STATUS:
                    continue
                state = packet.status()
                if state['reference'] != target or state['source'] != 1:
                    raise ProtocolError('Emitter selection/source changed')
                if state['busy']:
                    started = True
                if state['state'] == 5 and started:
                    failure = state
                    record(dict(type='screen_fpga_failure', target=target, status=state))
                    break
                if state['state'] != 2 or packet.token == last_token:
                    continue
                expected_stage, expected_repeat = divmod(len(samples), 3)
                if (packet.token != last_token + 1 or state['stage'] != expected_stage
                        or state['repeat'] != expected_repeat or expected_stage > 1
                        or state['output'] != (expected_stage == 1) or state['valid']):
                    raise ProtocolError('Unexpected screening sequence/output state')
                capture = await receiver.capture(raw=True)
                power, good = measured_power(capture)
                sample = dict(type='screen_measurement', target=target, stage=state['stage'],
                              repeat=state['repeat'], fpga=state, capture=capture,
                              power_adc2=power, good=bool(good))
                samples.append(sample)
                record(sample)
                # Never submit stage 1 repeat 2: FPGA must not advance into a
                # two-emitter phase test or reach any calibration commit path.
                if len(samples) == 6 or not good:
                    break
                await link.send(MEASURE, boot, packet.token, power, good, 1,
                                state['channel'] | state['stage'] << 8, state['repeat'])
                last_token = packet.token
    finally:
        stopped = await stop_and_confirm(link, boot)
        record(dict(type='screen_stopped', target=target, status=stopped))
    result = dict(target=target, **summarize(samples))
    if failure:
        result['fpga_error'] = failure['error']
    record(dict(type='screen_result', **result))
    return result


async def run(args):
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.with_suffix('.json').exists():
        raise ValueError('Summary already exists')
    with output.open('x', encoding='utf-8') as stream:
        def record(row):
            stream.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), **row)) + '\n')
            stream.flush()

        connection = SerialConnection(args.receiver_port)
        receiver = ReceiverClient(connection)
        link = None
        boot = None
        beat = None
        results = []
        try:
            link = FpgaLink(SerialConnection(args.fpga_port))
            initial = await link.receive()
            state = initial.status()
            boot = initial.boot
            count = state['rows'] * state['cols']
            if (state['source'] != 1 or state['busy'] or state['output'] or state['valid']
                    or state['state'] not in (0, 5)):
                raise ProtocolError('Requires idle/uncommitted automatic calibration endpoint')
            settings = Settings(tuple(round(v * 1000) for v in args.position_mm), args.level, 0)
            settings.validate(count)
            if not 0 <= args.first <= args.last < count:
                raise ValueError('Invalid channel range')
            record(dict(type='screen_initial', status=state, settings=vars(settings)))
            info = await receiver.info()
            record(dict(type='receiver_info', reply=info))
            if info['transport'] != 'USB':
                raise ProtocolError('Requires USB receiver')
            beat = asyncio.create_task(heartbeat(link, boot))
            # Bookend with known-good channel 0 to expose receiver/power drift.
            sequence = [0, *range(args.first, args.last + 1), 0]
            for index, target in enumerate(sequence):
                result = await screen_one(link, receiver, boot, target, settings, record, beat)
                result['control'] = index in (0, len(sequence) - 1)
                results.append(result)
                print(json.dumps(result), flush=True)
                if result['control'] and not result['screen_pass']:
                    raise ProtocolError('Control channel failed; screen cannot be trusted')
            document = dict(schema='touchsee-channel-screen-1', kind='diagnostic_only',
                            simulated=False, phase_verified=False, calibration_valid=False,
                            first=args.first, last=args.last, level=args.level,
                            position_um=list(settings.position_um), results=results)
            output.with_suffix('.json').write_text(json.dumps(document, indent=2), encoding='utf-8')
            record(dict(type='screen_complete', document=document))
            print('SCREEN COMPLETE; output stopped; no calibration committed:', output.resolve(), flush=True)
        finally:
            if beat:
                beat.cancel()
                with suppress(asyncio.CancelledError):
                    await beat
            try:
                if link and boot is not None:
                    stopped = await stop_and_confirm(link, boot)
                    record(dict(type='screen_final_state', status=stopped))
            finally:
                with suppress(Exception):
                    if receiver.can_stop:
                        await receiver.request('STOP')
                if link:
                    await link.close()
                await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fpga-port', required=True)
    parser.add_argument('--receiver-port', required=True)
    parser.add_argument('--first', type=int, default=32)
    parser.add_argument('--last', type=int, default=63)
    parser.add_argument('--position-mm', type=float, nargs=3, default=(0, 0, 70))
    parser.add_argument('--level', type=int, default=30)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.fpga_port.upper() == args.receiver_port.upper():
        parser.error('Transmitter and receiver ports must differ')
    try:
        asyncio.run(run(args))
    except (Exception, KeyboardInterrupt) as error:
        parser.exit(1, f'SCREEN STOPPED: {error}\n')


if __name__ == '__main__':
    main()
