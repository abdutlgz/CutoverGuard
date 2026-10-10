"""Build an offline CutoverGuard preview from saved synthetic local results."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cutoverguard.fixtures import fixture, available_fixtures
from cutoverguard.engine import reconcile
from cutoverguard.replay import build_replay

def main():
    examples=available_fixtures()
    bundles={item['id']:fixture(item['id']) for item in examples}
    reports={key:reconcile(bundle) for key,bundle in bundles.items()}
    out=ROOT/'cutover_results'/'local'
    out.mkdir(parents=True,exist_ok=True)
    for key,report in reports.items():
        (out/f'{key}-report.json').write_text(json.dumps(report,indent=2)+'\n')
    replays={kind:build_replay(kind) for kind in ('defective','fixed')}
    payload=json.dumps({'examples':examples,'bundles':bundles,'reports':reports,'replays':replays},ensure_ascii=True).replace('</','<\\/')
    shim='''const saved=PAYLOAD;
window.fetch=async(path,options={})=>{
 let value,error;
 if(path==='/api/examples')value={examples:saved.examples};
 else if(path.startsWith('/api/fixture?'))value=saved.bundles[new URLSearchParams(path.split('?')[1]).get('kind')];
 else if(path.startsWith('/api/replay?'))value=saved.replays[new URLSearchParams(path.split('?')[1]).get('scenario')];
 else if(path==='/api/reconcile'){
 const bundle=JSON.parse(options.body);
 const id=Object.keys(saved.bundles).find(k=>JSON.stringify(saved.bundles[k])===JSON.stringify(bundle));
 if(id)value=saved.reports[id];else error='Offline preview only supports its saved synthetic examples. Run cutover_app.py for arbitrary input.';
 }else error='Run cutover_app.py to validate saved reports. This is an offline preview.';
 return {ok:!error,status:error?400:200,json:async()=>structuredClone(error?{error}:value)};
};
'''.replace('PAYLOAD',payload)
    html=(ROOT/'cutover_static/index.html').read_text()
    css=(ROOT/'cutover_static/style.css').read_text()
    js=(ROOT/'cutover_static/app.js').read_text()
    html=html.replace('<link rel="stylesheet" href="/style.css">','<style>'+css+'</style>')
    html=html.replace('<script defer src="/app.js"></script>','')
    html=html.replace('<main>','<main><div style="padding:12px 24px;background:#fff0cc;color:#4f3700;font-size:14px">Offline preview · Saved synthetic local results · Start cutover_app.py for uploads, validation and fresh comparisons.</div>')
    html=html.replace('</body>','<script>'+shim.replace('</script','<\\/script')+'</script><script>'+js.replace('</script','<\\/script')+'</script></body>')
    (ROOT/'cutover-preview.html').write_text(html)
    print('Generated cutover-preview.html and saved synthetic local reports.')

if __name__=='__main__':main()
