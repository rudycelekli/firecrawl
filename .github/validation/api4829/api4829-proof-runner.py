"""Read-only remote native proof of an existing PR reconciliation; no publication."""
import hashlib,json,os,re,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
source=Path.cwd();assets=Path(os.environ['API_PROOF_ASSETS']);proof=Path(os.environ['API_PROOF_OUTPUT']);proof.mkdir(parents=True,exist_ok=True)
base='c22afa500aea53b8c0f3ff667a8debf3443bd3ee'
plan=json.loads((assets/'plan.json').read_text());patch=assets/'reconciliation.patch'
assert hashlib.sha256(patch.read_bytes()).hexdigest()==plan['patch_sha256']
def run(args,log=None,cwd=None):
 r=subprocess.run(args,cwd=cwd or source,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 if log:log.write_text(r.stdout)
 print(r.stdout,flush=True)
 if r.returncode:raise RuntimeError(f'{args}: exit {r.returncode}')
 return r.stdout.strip()
def raw(*args):return subprocess.check_output(['git',*args],cwd=source,text=True)
def git(*args):return raw(*args).strip()
def manifest(path):
 items=[{'path':name,'sha256':hashlib.sha256((source/name).read_bytes()).hexdigest()} for name in git('ls-files').splitlines() if (source/name).is_file()]
 tree=git('write-tree');path.write_text(json.dumps({'source_head':git('rev-parse','HEAD'),'index_tree':tree,'files':items},indent=2)+'\n');return tree
assert git('rev-parse','HEAD')==base
assert git('rev-parse','HEAD:apps/api/native')==plan['native_tree']
run(['git','apply','--check',str(patch)]);run(['git','apply',str(patch)])
run(['pnpm','knip','--cache'],proof/'knip-before-format.log',source/'apps/api')
run(['git','add','--',*plan['allowed_files']])
run(['pnpm','lint-staged'],proof/'lint-staged.log',source/'apps/api')
run(['git','diff','--exit-code'])
changed=git('diff','--cached','--name-only',base).splitlines();assert set(changed)==set(plan['allowed_files']),changed
(proof/'final-candidate.patch').write_text(raw('diff','--cached',base))
tree=manifest(proof/'candidate-manifest.json')
run(['pnpm','knip','--cache'],proof/'knip.log',source/'apps/api')
run(['timeout','-k','30s','10m','pnpm','exec','tsc','--noEmit'],proof/'tsc.log',source/'apps/api')
assert git('write-tree')==tree;run(['git','diff','--exit-code'])
def native(label,tests,expected_name=None,expected_reason=None):
 xml=proof/f'{label}.xml';cmd=['timeout','-k','45s','15m','pnpm','harness','pnpm','exec','vitest','run',*tests,'--maxWorkers=1','--reporter=default','--reporter=junit',f'--outputFile.junit={xml}']
 r=subprocess.run(cmd,cwd=source/'apps/api',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);(proof/f'{label}.log').write_text(r.stdout);print(r.stdout,flush=True)
 assert xml.exists(),f'No native report for {label}; exit {r.returncode}'
 cases=list(ET.parse(xml).iter('testcase'));failed=[c for c in cases if c.find('failure') is not None or c.find('error') is not None];skipped=[c for c in cases if c.find('skipped') is not None]
 receipt={'exit':r.returncode,'cases':len(cases),'failures':len(failed),'skipped':len(skipped),'failed_names':[c.get('name') for c in failed]}
 assert cases and not skipped
 if expected_name:
  matches=[]
  for case in failed:
   node=case.find('failure') if case.find('failure') is not None else case.find('error');reason=(node.get('message')or'')+' '+(node.text or '')
   if re.search(expected_name,case.get('name')or'') and re.search(expected_reason,reason,re.S):matches.append(case.get('name'))
  assert r.returncode!=0 and matches,f'No causal mutation failure for {label}: {receipt}'
  receipt['causal_matches']=matches
 else:assert r.returncode==0 and not failed,receipt
 (proof/f'{label}-result.json').write_text(json.dumps(receipt,indent=2)+'\n')
controller=source/'apps/api/src/controllers/v2/scrape-browser.ts';candidate=controller.read_text()
start=candidate.index('          if (req.body.existingSessionId)');end=candidate.index('          if (session) return;',start)
adoption=candidate[start:end];needle='              if (signal.aborted) throw signal.error;';assert adoption.count(needle)==2
controller.write_text(candidate[:start]+adoption.replace(needle,'')+candidate[end:])
manifest(proof/'adoption-mutant-manifest.json')
native('adoption-mutant',plan['focused_tests'],r"adopted private browser lease during (lookup|link)",r'200.*503|503.*200')
controller.write_text(candidate)
needle='updateBrowserSessionScrapeId(created.session.id, scrapeId)';assert candidate.count(needle)==1
controller.write_text(candidate.replace(needle,'Promise.resolve()'))
manifest(proof/'persistence-mutant-manifest.json')
native('persistence-mutant',plan['focused_tests'],r'only one initialized private browser',r'1.*2|2.*1')
controller.write_text(candidate)
run(['git','diff','--exit-code']);assert git('write-tree')==tree
native('after',plan['tests'])
run(['git','diff','--exit-code']);assert git('write-tree')==tree and git('rev-parse','HEAD')==base
manifest(proof/'final-manifest.json')
(proof/'complete.json').write_text(json.dumps({'pr':4829,'comparison_base':base,'tested_index_tree':tree,'knip':'PASS','lint_staged':'PASS','typecheck':'PASS','native_tests':'PASS','before_kind':'labelled adoption-guard and missing-persistence coverage mutants; historical old-head VM proof is separate','remote_publication':False},indent=2)+'\n')
