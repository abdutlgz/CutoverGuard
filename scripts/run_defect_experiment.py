"""Run a seeded synthetic defect study and save immutable evidence files."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cutoverguard.defect_experiment import run_experiment, markdown_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261010)
    parser.add_argument('--trials', type=int, default=100, help='Trials per category (7 categories)')
    parser.add_argument('--rows', type=int, default=100)
    parser.add_argument('--output', type=Path, default=Path('experiment_results/seeded-defects-20261010.json'))
    args = parser.parse_args()
    markdown = args.output.with_suffix('.md')
    if args.output.exists() or markdown.exists() or args.output == markdown:
        parser.error('Evidence path exists or is not a distinct JSON path; choose a new --output filename.')
    try:
        result = run_experiment(seed=args.seed, trials_per_category=args.trials, rows=args.rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation guards against accidental overwrite, including concurrent runs.
        with args.output.open('x') as handle:
            json.dump(result, handle, indent=2)
            handle.write('\n')
        with markdown.open('x') as handle:
            handle.write(markdown_report(result))
        print(json.dumps({'success': result['success'], 'seed': result['seed'], 'total_trials': result['total_trials'], 'experiment_id': result['experiment_id'], 'output': str(args.output), 'summary': result['summary']}, indent=2))
        return 0 if result['success'] else 1
    except (ValueError, OSError) as exc:
        print('Defect experiment: ' + str(exc), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
