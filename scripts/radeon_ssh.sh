#!/usr/bin/env bash
# Run in your normal Terminal. Keys stay in ~/.ssh, outside the project.
set -euo pipefail
key_path="$HOME/.ssh/knighthacks_radeon_2026"
action="${1:---help}"
case "$action" in
  --generate)
    if [[ -e "$key_path" || -e "$key_path.pub" ]]; then
      echo 'Key already exists; refusing to overwrite. Use --public to view its public half.' >&2
      exit 1
    fi
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$key_path" -C 'knighthacks-radeon-2026'
    echo 'Register this public key in the Radeon portal before launching an SSH-enabled instance:'
    cat "$key_path.pub"
    ;;
  --public)
    [[ -f "$key_path.pub" ]] || { echo 'No public key; run --generate in your Terminal.' >&2; exit 1; }
    cat "$key_path.pub"
    ;;
  --connect|--probe|--benchmark)
    if [[ "$action" == '--benchmark' ]]; then
      [[ $# -ge 4 && $# -le 7 ]] || { echo 'Usage: bash scripts/radeon_ssh.sh --benchmark HOST PORT USER [ROWS [ACCOUNTS [REPEATS]]]' >&2; exit 1; }
      bench_rows="${5:-1000000}"; bench_accounts="${6:-10000}"; bench_repeats="${7:-5}"
      for number in "$bench_rows" "$bench_accounts" "$bench_repeats"; do
        [[ "$number" =~ ^[1-9][0-9]*$ && ${#number} -le 8 ]] || { echo 'Benchmark sizes must be positive decimal integers without leading zeros.' >&2; exit 1; }
      done
      [[ $bench_rows -ge 2 && $bench_rows -le 10000000 && $bench_accounts -ge 2 && $bench_accounts -le $bench_rows && $bench_repeats -le 20 ]] || { echo 'Require 2..10M rows, 2..rows accounts, 1..20 repeats.' >&2; exit 1; }
    else
      [[ $# == 4 ]] || { echo 'Usage: bash scripts/radeon_ssh.sh --connect|--probe HOST PORT USER' >&2; exit 1; }
    fi
    host="$2"; port="$3"; remote_user="$4"
    [[ "$host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && "$remote_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ && "$port" =~ ^[0-9]{1,5}$ ]] || { echo 'Use hostname, numeric port and username from the portal.' >&2; exit 1; }
    [[ $port -ge 1 && $port -le 65535 ]] || { echo 'Invalid port.' >&2; exit 1; }
    [[ -f "$key_path" ]] || { echo 'Private key missing; run --generate in your Terminal.' >&2; exit 1; }
    if [[ "$action" == '--probe' || "$action" == '--benchmark' ]]; then
      repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
      probe_path="$repo_root/amd/check_rocm.py"
      if [[ "$action" == '--benchmark' ]]; then probe_path="$repo_root/amd/benchmark_allocations.py"; fi
      [[ -f "$probe_path" ]] || { echo 'Missing AMD script.' >&2; exit 1; }
      remote_probe_command='
first_python=
torch_python=
for name in python3 python /opt/venv/bin/python /opt/conda/bin/python /venv/bin/python /usr/local/bin/python3; do
  resolved=$(command -v "$name" 2>/dev/null) || continue
  [ -x "$resolved" ] || continue
  [ -n "$first_python" ] || first_python="$resolved"
  if "$resolved" -c "import torch; assert torch.version.hip" >/dev/null 2>&1; then
    exec "$resolved" - "$@"
  fi
  if "$resolved" -c "import torch" >/dev/null 2>&1; then torch_python="$resolved"; fi
done
if [ -n "$torch_python" ]; then exec "$torch_python" - "$@"; fi
if [ -n "$first_python" ]; then exec "$first_python" - "$@"; fi
echo "No Python interpreter found on PATH or common environment paths. Check the selected container image." >&2
exit 127
'
      if [[ "$action" == '--benchmark' ]]; then
        remote_probe_command='set -- --rows '"$bench_rows"' --accounts '"$bench_accounts"' --repeats '"$bench_repeats"' --output "/tmp/cutoverguard-benchmark-$(date +%s)-$$.json"
'"$remote_probe_command"
        mkdir -p "$repo_root/amd_results"
        benchmark_tag="${bench_rows}rows"
        if [[ "$bench_rows" == 1000000 ]]; then benchmark_tag='1m'; fi
        if [[ "$bench_rows" == 10000000 ]]; then benchmark_tag='10m'; fi
        local_evidence="$(mktemp "$repo_root/amd_results/benchmark-${benchmark_tag}.XXXXXX")"
        if ssh -o IdentitiesOnly=yes -o StrictHostKeyChecking=ask -o ConnectTimeout=15 -i "$key_path" -p "$port" "$remote_user@$host" "$remote_probe_command" < "$probe_path" > "$local_evidence"; then
          mv "$local_evidence" "$local_evidence.json"
          cat "$local_evidence.json"
          echo "Saved benchmark evidence: $local_evidence.json" >&2
        else
          code=$?
          echo "Benchmark UNVERIFIED (exit $code). Partial output: $local_evidence" >&2
          exit "$code"
        fi
        exit 0
      fi
      exec ssh -o IdentitiesOnly=yes -o StrictHostKeyChecking=ask -o ConnectTimeout=15 -i "$key_path" -p "$port" "$remote_user@$host" "$remote_probe_command" < "$probe_path"
    fi
    exec ssh -o IdentitiesOnly=yes -o StrictHostKeyChecking=ask -o ConnectTimeout=15 -i "$key_path" -p "$port" "$remote_user@$host"
    ;;
  *)
    echo 'bash scripts/radeon_ssh.sh --generate           # no overwrites; interactive passphrase'
    echo 'bash scripts/radeon_ssh.sh --public             # public half only'
    echo 'bash scripts/radeon_ssh.sh --connect HOST PORT USER'
    echo 'bash scripts/radeon_ssh.sh --probe HOST PORT USER # run ROCm check without uploads or installs'
    echo 'bash scripts/radeon_ssh.sh --benchmark HOST PORT USER [ROWS [ACCOUNTS [REPEATS]]] # defaults 1M/10k/5; up to 10M rows'
    echo 'Example: bash scripts/radeon_ssh.sh --benchmark HOST PORT USER 10000000 10000 5'
    echo 'Use the hackathon portal values, PyTorch ROCm 10 and SSH enabled before launch.'
    ;;
esac
