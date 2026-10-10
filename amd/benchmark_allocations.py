"""Compare CPU/ROCm exact synthetic account aggregation, not full cutover validation."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time


def reference_ledger(rows, accounts):
    """Independent Python-int oracle for the declared synthetic allocation workload."""
    if not (2 <= rows <= 10_000_000 and 2 <= accounts <= rows):
        raise ValueError('Require 2..10M rows and 2..rows accounts.')
    source, candidate = [0] * accounts, [0] * accounts
    for i in range(rows):
        account = i % accounts
        value = 100000 if i == 0 else 200000 if i == 1 else i % 100000 + 1
        dest = 1 if i == 0 else 0 if i == 1 else account
        source[account] += value
        candidate[dest] += value
    return source, candidate


def measure_python_baseline(rows, accounts, repeats, oracle, clock=time.perf_counter):
    """Scalar-loop baseline includes formula generation and accumulation.

    This is deliberately NOT the full validation engine. Tensor inputs are already
    prepared, so its timing boundary differs and is recorded rather than hidden.
    """
    durations = []
    for repeat in range(repeats + 1):
        start = clock()
        actual = reference_ledger(rows, accounts)
        duration = clock() - start
        if actual != oracle:
            raise RuntimeError('Naive Python baseline disagrees with independent ledger oracle.')
        if repeat:
            durations.append(duration)
    return {'label': 'naive Python allocation loop', 'seconds': durations,
            'median_seconds': statistics.median(durations),
            'timing_boundary': 'Python list allocation + generated account/value loop + integer accumulation; no transfers; not full reconciliation',
            'warmups_excluded': 1, 'exact_oracle_agreement': True}


def measure_tensor_mode(mode, torch, source, candidate, amounts, accounts, repeats, oracle, clock=time.perf_counter):
    """Time one declared tensor boundary, always verify host outputs exactly."""
    if mode not in ('cpu', 'rocm', 'rocm_resident'):
        raise ValueError('Unsupported tensor benchmark mode')
    def compute(src, dst, value):
        a = torch.zeros(accounts, dtype=torch.int64, device=value.device)
        b = torch.zeros_like(a)
        a.index_add_(0, src, value)
        b.index_add_(0, dst, value)
        return a, b
    resident = None
    if mode == 'rocm_resident':
        resident = (source.to('cuda'), candidate.to('cuda'), amounts.to('cuda'))
        torch.cuda.synchronize()
    durations = []
    for repeat in range(repeats + 1):
        if mode != 'cpu':
            torch.cuda.synchronize()
        start = clock()
        if mode == 'rocm':
            src, dst, value = source.to('cuda'), candidate.to('cuda'), amounts.to('cuda')
        elif resident is not None:
            src, dst, value = resident
        else:
            src, dst, value = source, candidate, amounts
        a, b = compute(src, dst, value)
        if mode == 'rocm_resident':
            torch.cuda.synchronize()
            duration = clock() - start
            # Output transfer and exact verification happen outside resident timing.
            actual = (a.cpu().tolist(), b.cpu().tolist())
        else:
            actual = (a.cpu().tolist(), b.cpu().tolist())
            if mode == 'rocm':
                torch.cuda.synchronize()
            duration = clock() - start
        if actual != oracle:
            raise RuntimeError('Exact agreement failed; benchmark result must not be used.')
        if repeat:
            durations.append(duration)
        del a, b, src, dst, value
    if resident is not None:
        del resident
    boundaries = {
        'cpu': 'CPU tensor output allocation + aggregation + host list conversion; input construction excluded',
        'rocm': 'host-to-device transfer + GPU output allocation/aggregation + output-to-host transfer/list conversion + synchronization; input construction excluded',
        'rocm_resident': 'GPU output allocation + aggregation + synchronization; initial input upload and each output-to-host copy/list verification excluded',
    }
    return {'label': {'cpu': 'CPU tensor aggregation', 'rocm': 'ROCm aggregation including transfers', 'rocm_resident': 'ROCm repeated check with resident inputs'}[mode],
            'seconds': durations, 'median_seconds': statistics.median(durations),
            'timing_boundary': boundaries[mode], 'warmups_excluded': 1,
            'exact_oracle_agreement': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=1_000_000)
    parser.add_argument('--accounts', type=int, default=10_000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', type=Path, default=Path('amd-benchmark.json'))
    args = parser.parse_args()
    if not (2 <= args.rows <= 10_000_000 and 2 <= args.accounts <= args.rows and 1 <= args.repeats <= 20):
        parser.error('Require 2..10M rows, 2..rows accounts, 1..20 repeats.')
    if args.output.exists():
        parser.error('Evidence file exists; select a new --output filename.')
    try:
        import torch
    except ImportError:
        parser.error('Use the AMD PyTorch ROCm 10 image.')
    if not torch.version.hip or not torch.cuda.is_available():
        parser.error('No accessible ROCm GPU. Run check_rocm.py first.')
    # Bounded arithmetic: at most 10M * 100000 + 100000 minor units; within int64.
    # Integer account indices are an internal mapping, not raw identifier casts.
    event = torch.arange(args.rows, dtype=torch.int64)
    source = event.remainder(args.accounts)
    candidate = source.clone()
    candidate[0], candidate[1] = source[1].clone(), source[0].clone()
    amounts = event.remainder(100000) + 1
    amounts[0], amounts[1] = 100000, 200000
    del event
    reference_source, reference_candidate = reference_ledger(args.rows, args.accounts)
    oracle = (reference_source, reference_candidate)
    timings = {'naive_python': measure_python_baseline(args.rows, args.accounts, args.repeats, oracle)}
    for mode in ('cpu', 'rocm', 'rocm_resident'):
        timings[mode] = measure_tensor_mode(mode, torch, source, candidate, amounts,
                                           args.accounts, args.repeats, oracle)
    deltas = [b-a for a,b in zip(reference_source,reference_candidate)]
    assert deltas[0] == 100000 and deltas[1] == -100000 and sum(deltas) == 0
    assert sum(abs(delta) for delta in deltas) == 200000
    report = {'data_kind':'synthetic', 'scope':'allocation aggregation only; not full reconciliation',
              'rows':args.rows, 'accounts':args.accounts, 'currency':'USD', 'minor_unit_scale':2,
              'device':torch.cuda.get_device_name(0), 'torch_version':torch.__version__,
              'hip_version':torch.version.hip, 'cpu_threads':torch.get_num_threads(), 'exact_python_oracle_agreement':True,
              'benchmark_version':'2.0', 'timing_boundary':'Per-mode boundaries below; one warmup excluded. Tensor input construction and oracle verification excluded; naive scalar formula generation included.',
              'timings':timings, 'cpu_over_rocm_ratio':timings['cpu']['median_seconds']/timings['rocm']['median_seconds'],
              'net_delta_minor':str(sum(deltas)), 'absolute_account_delta_minor':str(sum(abs(d) for d in deltas)),
              'changed_accounts':[{'account_id':f'{i:08d}', 'delta_minor':str(d)} for i,d in enumerate(deltas) if d],
              'limitations':['Synthetic workload only. No production speedup extrapolation.',
                             'No identifier, duplicate, currency or completeness checks; main engine remains authoritative.',
                             'Naive Python timing includes formula generation; tensor inputs are preconstructed. These boundaries differ and naive timing is not the production engine baseline.',
                             'Resident GPU timing excludes initial upload and output retrieval; it is not end-to-end latency. Every output is checked outside that timing.',
                             'This compares allocation aggregation paths with exact Python-int agreement; no full engine or replay acceleration is demonstrated.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
