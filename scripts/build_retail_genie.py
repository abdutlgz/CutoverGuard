"""Prepare retail-only Genie instructions and SQL examples; makes no cloud calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cutover_app import validate_report
from scripts.build_retail_dashboard import build,comparison


def prepare(source,output,corrected=None):
    source=Path(source);output=Path(output)
    if output.exists():
        raise ValueError('Choose a fresh output folder.')
    report=validate_report(json.loads(source.read_text()))
    if report['input_bundle']['dataset_info']['controlled_case']!='ownership_swaps':
        raise ValueError('This explanation packet is for the controlled defective retail run.')
    dashboard=build(report)
    queries={d['name']:''.join(d['queryLines']) for d in dashboard['datasets']}
    tables=report['execution']['tables']
    totals=report['currency_totals'][0]
    currency=totals['currency']
    def major(value):
        amount=int(value);whole,cents=divmod(abs(amount),100)
        return ('-' if amount<0 else '')+str(whole)+'.'+str(cents).zfill(2)
    effects=', '.join(r['account_id']+': '+major(r['delta_minor']) for r in report['account_impacts'] if int(r['delta_minor'])!=0)
    instructions=[
        'These are historical UCI Online Retail invoice lines with deliberately injected candidate ownership defects, not observed bank migration failures. Customer-derived receivables buckets are not bank accounts; invoice amounts are not cash settlements.',
        f'This space contains only the controlled defective run {report["run_id"]}. No corrected run is registered. Do not claim a correction succeeded without its separate SQL evidence.',
        'Query only the three registered Gold tables using SELECT. Keep source and candidate distinct. Monetary fields ending _minor are integer minor units; divide by 100 using DECIMAL(38,2) in SQL for GBP/USD/EUR. State currency and never sum across currencies.',
        'Zero net difference does not establish ownership parity. Absolute account differences count both sides of allocation changes. Affected transaction notional is a separate measure. Neither measure is money lost, savings or regulatory exposure.',
        'Every standalone answer must identify this as a controlled historical invoice demonstration. Call account totals receivables bucket totals, not bank balances, and describe the net as extract net, not firm cash. Limit parity claims to supplied records and implemented checks; never say perfect migration or unrestricted every-transaction correctness.',
        'A signed bucket delta is the net effect of changed invoice assignments, not necessarily the amount of either changed invoice. Do not describe equal and opposite deltas as two invoices of that amount or as an actual cash transfer. If invoice amounts are unavailable in the registered Gold tables, say so rather than infer them from aggregate deltas.',
        'Use signed delta_minor (candidate minus source) for account-change charts. Do not substitute source or candidate balances for signed differences. Preserve raw identifiers as strings.',
        'Genie explains computed evidence. It must not approve a production cutover or claim regulatory compliance. Ready for review means supported checks passed under supplied completeness declarations; human review remains required.',
        'State uncertainty if a query fails or evidence is missing. Do not invent observations, metrics, migration failure rates or client affiliation. Never propose automatic remediation or write SQL.',
        'Source: UCI Online Retail, DOI 10.24432/C5BW33, CC BY 4.0. Execution provenance was supplied through a user-terminal report and locally recalculated, not independently authenticated by this agent.'
    ]
    examples=[
        ('Why is this controlled candidate blocked even though the total matches?',queries['retail_kpis']),
        ('Which invoice-line ownership assignments changed?',queries['retail_findings']),
        (f'Show the signed changes for affected receivables buckets in {currency}.',queries['retail_chart']),
        ('Has a corrected retail candidate been verified in this space?', 'SELECT \'No corrected retail run is registered in this space; do not infer verification.\' AS evidence_scope')
    ]
    fixed=None
    if corrected:
        fixed=validate_report(json.loads(Path(corrected).read_text()))
        paired=comparison(report,fixed)
        summary=next(''.join(d['queryLines']) for d in paired['datasets'] if d['name']=='retail_comparison')
        instructions[1]=f'This space registers two controlled historical runs: defective {report["run_id"]} and corrected exact baseline {fixed["run_id"]}. Keep their tables and scenario labels separate. The same source is preserved. The corrected run passed supported checks and is ready for human review, not an automatic production cutover.'
        instructions[2]=instructions[2].replace('three registered','six registered')
        examples[-1]=('Did the corrected candidate restore ownership parity?',summary)
    uid=lambda value:hashlib.sha256(value.encode()).hexdigest()[:32]
    space={'version':2,'config':{'sample_questions':[{'id':uid('sample'+q),'question':[q]} for q,_ in examples]},
           'data_sources':{'tables':[{'identifier':tables[name].replace('`',''),'description':[f'Controlled defective historical retail {name}; run {report["run_id"]}.']} for name in ('issues','impacts','totals')]},
           'instructions':{'text_instructions':[{'id':uid('retail-policy'),'content':[line+'\n' for line in instructions]}],
                           'example_question_sqls':[{'id':uid('example'+q),'question':[q],'sql':[sql]} for q,sql in examples]}}
    if fixed:
        space['data_sources']['tables'].extend({'identifier':fixed['execution']['tables'][name].replace('`',''),'description':[f'Corrected exact baseline historical retail {name}; run {fixed["run_id"]}.']} for name in ('issues','impacts','totals'))
    for collection in (space['config']['sample_questions'],space['instructions']['text_instructions'],space['instructions']['example_question_sqls']):
        collection.sort(key=lambda row:row['id'])
    space['data_sources']['tables'].sort(key=lambda row:row['identifier'])
    output.mkdir(parents=True)
    (output/'genie-space.json').write_text(json.dumps(space,indent=2)+'\n')
    (output/'instructions.txt').write_text('\n\n'.join(instructions)+'\n')
    (output/'examples.sql').write_text('\n\n'.join('-- '+q+'\n'+sql.rstrip()+';' for q,sql in examples)+'\n')
    (output/'README.md').write_text(f'''# Retail Genie setup and review

Create a NEW Genie space called CutoverGuard Retail Review using the existing SQL
warehouse {report['execution']['warehouse_id']}. Keep the synthetic-only space separate. Add these three
Gold tables using the data picker:

'''+ '\n'.join('- '+r['identifier'] for r in space['data_sources']['tables'])+'''

Paste instructions.txt into the space's general text instructions. Add the example
questions and SQL from examples.sql through the space's SQL instruction editor.
genie-space.json is serialized Genie configuration for future API use, NOT a
dashboard import file. No cloud space has been created by this script.

'''+f'''Ask the first three questions. Compare answers and generated SQL against the saved
report: {report['summary']['primary_issue_count']} ownership findings; currency {currency}; source total {major(totals['source_minor'])}; candidate total {major(totals['candidate_minor'])}; net {major(totals['net_delta_minor'])}; absolute differences
{major(totals['absolute_account_delta_minor'])}; affected notional {major(totals['affected_notional_minor'])}; signed bucket changes {effects}. These values
are specific to the current saved run. Verify with a table as well as a chart.
'''+('Ask the fourth question: compare both SQL runs; corrected must show zero findings, net, absolute differences and affected notional, while preserving totals. This means ready for human review, not automatic approval.\n' if fixed else 'Ask the fourth question: it must say corrected retail verification is unavailable in this space. Do not register a corrected run until its full SQL/oracle check passes.\n')+'''
No answer evaluation, space creation or dashboard linking is claimed yet.
''')
    if fixed:
        notes=(output/'README.md').read_text().replace('Add these three','Add these six')
        (output/'README.md').write_text(notes)
    return space


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,default=ROOT/'databricks_files/retail-run-002/report.json')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--corrected',type=Path,help='Verified exact baseline report for paired explanations')
    args=p.parse_args()
    prepare(args.report,args.output,args.corrected)
    print('Retail Genie packet prepared; cloud creation and answers UNVERIFIED:',args.output)
