# Optional AMD scale evidence

Purpose: benchmark exact account/currency allocation aggregation on a larger
synthetic ledger. This is a component benchmark, not full migration validation.
The main engine remains authoritative for identifiers, duplicates, completeness,
transaction fields and quarantine. No AI model is required for exact arithmetic.

Use the organizer-specified **PyTorch ROCm 10** image. Already root: no sudo.
Upload only these original scripts to the machine; no private client data or secrets.
The supplied instance endpoint is root@YOUR_AMD_HOST, port YOUR_SSH_PORT. Local key:
~/.ssh/knighthacks_radeon_2026. This endpoint belongs to the current instance only.
The agent session's SSH attempt was blocked by its network sandbox; connectivity
and ROCm verification remain pending the user's normal Terminal.
Do not paste private-key contents or passwords into chat.

From the local KNIGHTHACKS Terminal, run the probe without uploading files:

```sh
bash scripts/radeon_ssh.sh --probe YOUR_AMD_HOST YOUR_SSH_PORT root
```

The script streams only amd/check_rocm.py to remote Python, installs nothing,
and prints device/ROCm details plus an exact integer allocation smoke result.
It discovers python3/common environments and prefers ROCm-enabled PyTorch.
To open an interactive shell instead, use --connect with the same arguments.
On first connection, check the host fingerprint before accepting it; host-key
mismatch handling is manual, never automatically bypassed by this script.

In the remote shell where you uploaded the files:

```sh
python check_rocm.py
python benchmark_allocations.py --rows 100000 --accounts 10000 --output benchmark-100k.json
python benchmark_allocations.py --rows 1000000 --accounts 10000 --output benchmark-1m.json
```

First command must verify `hip_version`, the GPU and the int64 allocation operation.
Benchmark code refuses a non-ROCm device and fails if any account result differs
from an independent Python-integer ledger. Report CPU/GPU times as measured; GPU
may be slower for small data. Timing includes tensor aggregation, transfer and
host output conversion; excludes fixture creation/oracle construction. One warmup
is excluded. Different runtimes are not equivalent to production speedups.

The intentionally swapped first two transactions produce net delta zero and
account deltas +100000/-100000 USD minor units. Sum of absolute differences is
200000, not a loss. Eight-character account IDs are generated from explicit
indices; no raw account identifier is cast to a number. One currency only.
Bounds ensure every intermediate is comfortably within int64 range.

PyTorch's HIP backend uses `torch.cuda` APIs even on AMD:
[official HIP semantics](https://docs.pytorch.org/docs/stable/notes/hip.html).
The GPU aggregation is checked exactly on every repetition; no result or speedup
claim is accepted without execution. PyTorch documents potential device
nondeterminism for index_add; bounded integer agreement is explicitly tested here:
[index_add reference](https://docs.pytorch.org/docs/stable/generated/torch.Tensor.index_add_.html).

Current evidence: the user Terminal probe verified Python 3.14.4, PyTorch
2.13.0+rocm10.0.0, HIP 7.15.26333, AMD Radeon Pro W7900D, and exact int64
allocation. Saved supplied output: amd_results/rocm-probe-user.json. Agent direct
SSH remains blocked. The user's completed 1M-row benchmark is saved in
amd_results/benchmark-1m.VAll9q.json: CPU median 2.215 ms, ROCm median 1.400 ms,
ratio 1.582×, or 36.8% less elapsed time (0.815 ms absolute difference).
Five measured repetitions per device matched the independent integer oracle.
These measurements include aggregation, transfers and host list conversion;
exclude source preparation and oracle construction. They support a claim about
this synthetic component workload, not full reconciliation or production speedup.

Run the 1M-row benchmark from the local KNIGHTHACKS Terminal:

```sh
bash scripts/radeon_ssh.sh --benchmark YOUR_AMD_HOST YOUR_SSH_PORT root
```

This streams the original benchmark to remote Python, uses five measured repeats
and a separate warmup for each device, writes a unique temporary remote JSON, and
saves stdout evidence in a unique local amd_results/benchmark-1m.*.json. It installs
nothing and uploads no data or secrets. Failure retains partial stdout labeled
unverified; only successful measured results may support a performance claim.
Actual AMD use may support its challenge; eligibility is decided by sponsor judges.
