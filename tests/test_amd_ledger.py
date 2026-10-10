"""Pure-integer benchmark oracle checks; no claim of PyTorch or GPU execution."""
import pytest
from amd.benchmark_allocations import reference_ledger


def test_tiny_reference_is_hand_checked_ownership_swap():
    assert reference_ledger(2, 2) == ([100000, 200000], [200000, 100000])


@pytest.mark.parametrize('rows,accounts', [(101, 3), (10000, 97), (100003, 10000)])
def test_benchmark_oracle_preserves_total_and_only_swaps_first_two_accounts(rows, accounts):
    source, candidate = reference_ledger(rows, accounts)
    deltas = [b-a for a,b in zip(source,candidate)]
    assert deltas[:2] == [100000, -100000]
    assert all(delta == 0 for delta in deltas[2:])
    assert sum(source) == sum(candidate)
    assert sum(abs(delta) for delta in deltas) == 200000
    assert sum(source) == sum(i % 100000 + 1 for i in range(rows)) + 99999 + 199998


@pytest.mark.parametrize('rows,accounts', [(1,2), (3,4), (10000001,2)])
def test_out_of_scope_sizes_rejected_before_large_allocation(rows, accounts):
    with pytest.raises(ValueError):reference_ledger(rows,accounts)


def test_naive_timing_warmup_boundary_and_exact_agreement():
    from amd.benchmark_allocations import measure_python_baseline
    ticks = iter([0, 9, 10, 12, 20, 24])
    result = measure_python_baseline(2, 2, 2, ([100000, 200000], [200000, 100000]), clock=lambda: next(ticks))
    assert result['seconds'] == [2, 4]
    assert result['median_seconds'] == 3
    assert result['exact_oracle_agreement']
    assert 'generated account/value' in result['timing_boundary']
    with pytest.raises(RuntimeError, match='disagrees'):
        measure_python_baseline(2, 2, 1, ([0, 0], [0, 0]))


class FakeTensor:
    def __init__(self, values, device, trace):
        self.values, self.device, self.trace = list(values), device, trace
    def to(self, device):
        self.trace.append('upload')
        return FakeTensor(self.values, device, self.trace)
    def cpu(self):
        self.trace.append('download')
        return self
    def tolist(self):
        self.trace.append('list')
        return self.values[:]
    def index_add_(self, dim, indices, values):
        assert dim == 0
        self.trace.append('aggregate')
        for account, amount in zip(indices.values, values.values):
            self.values[account] += amount


class FakeTorch:
    int64 = 'int64'
    def __init__(self, trace):
        self.trace = trace
        self.cuda = self
    def synchronize(self):
        self.trace.append('synchronize')
    def zeros(self, count, dtype, device):
        assert dtype == 'int64'
        return FakeTensor([0] * count, device, self.trace)
    def zeros_like(self, tensor):
        return FakeTensor([0] * len(tensor.values), tensor.device, self.trace)


@pytest.mark.parametrize('mode,uploads', [('cpu', 0), ('rocm', 9), ('rocm_resident', 3)])
def test_tensor_timing_boundaries_and_every_repeat_exact(mode, uploads):
    """Fake tensor transport proves timing policy, not GPU performance/execution."""
    from amd.benchmark_allocations import measure_tensor_mode
    trace = []
    torch = FakeTorch(trace)
    src = FakeTensor([0, 1], 'cpu', trace)
    dst = FakeTensor([1, 0], 'cpu', trace)
    value = FakeTensor([100000, 200000], 'cpu', trace)
    tick = iter(range(6))
    def clock():
        trace.append('clock')
        return next(tick)
    result = measure_tensor_mode(mode, torch, src, dst, value, 2, 2, ([100000, 200000], [200000, 100000]), clock=clock)
    assert result['seconds'] == [1, 1]
    assert result['median_seconds'] == 1
    assert trace.count('upload') == uploads
    assert trace.count('download') == 6
    if mode == 'rocm_resident':
        first_stop = [i for i, item in enumerate(trace) if item == 'clock'][1]
        assert trace.index('download') > first_stop
        assert trace.index('upload') < trace.index('clock')
        assert 'excluded' in result['timing_boundary']
    elif mode == 'rocm':
        first_stop = [i for i, item in enumerate(trace) if item == 'clock'][1]
        assert trace.index('download') < first_stop
    assert result['exact_oracle_agreement']


def test_resident_timing_rejects_wrong_oracle_even_outside_clock():
    from amd.benchmark_allocations import measure_tensor_mode
    trace = []
    torch = FakeTorch(trace)
    with pytest.raises(RuntimeError, match='agreement failed'):
        measure_tensor_mode('rocm_resident', torch, FakeTensor([0, 1], 'cpu', trace), FakeTensor([1, 0], 'cpu', trace), FakeTensor([100000, 200000], 'cpu', trace), 2, 1, ([1, 2], [2, 1]))


def test_remote_benchmark_sizes_reject_before_ssh_or_key_access():
    import subprocess
    for args in [('10000001',), ('10', '11'), ('10000000', '10000', '21'), ('1;echo',), ('01000000',)]:
        result = subprocess.run(['bash', 'scripts/radeon_ssh.sh', '--benchmark', 'example.invalid', '2222', 'root', *args], text=True, capture_output=True)
        assert result.returncode != 0
        assert 'Private key missing' not in result.stderr
        assert 'UNVERIFIED' not in result.stderr
