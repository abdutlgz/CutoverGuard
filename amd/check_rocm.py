"""Read-only device probe for the AMD PyTorch ROCm 10 image. No installs or sudo."""
import json
import platform
import sys


def main():
    info = {'python': platform.python_version(), 'platform': platform.system()}
    try:
        import torch
    except ImportError:
        info.update(status='unavailable', reason='PyTorch is not installed; select the supplied PyTorch ROCm 10 image.')
        print(json.dumps(info, indent=2))
        return 1
    info.update(torch_version=torch.__version__, hip_version=torch.version.hip,
                gpu_available=torch.cuda.is_available())
    if not torch.version.hip or not torch.cuda.is_available():
        info.update(status='unavailable', reason='An accessible ROCm GPU is required for the AMD benchmark.')
        print(json.dumps(info, indent=2))
        return 1
    info['devices'] = [{'index': i, 'name': torch.cuda.get_device_name(i),
                         'memory_bytes': torch.cuda.get_device_properties(i).total_memory}
                        for i in range(torch.cuda.device_count())]
    try:
        amounts = torch.tensor([100000, 200000], dtype=torch.int64, device='cuda')
        accounts = torch.tensor([1, 0], dtype=torch.int64, device='cuda')
        result = torch.zeros(2, dtype=torch.int64, device='cuda').index_add_(0, accounts, amounts)
        assert result.cpu().tolist() == [200000, 100000]
        info.update(status='verified', int64_allocation_smoke_test=True)
    except Exception as error:
        info.update(status='unverified', reason=type(error).__name__, int64_allocation_smoke_test=False)
        print(json.dumps(info, indent=2))
        return 1
    print(json.dumps(info, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
