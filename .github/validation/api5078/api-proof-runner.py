"""Remote isolated native proof only. No commits, pushes or secrets."""
import hashlib,json,os,re,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path
source=Path.cwd(); assets=Path(os.environ['API_PROOF_ASSETS']); proof=Path(os.environ['API_PROOF_OUTPUT']);proof.mkdir(parents=True,exist_ok=True)
plans=json.loads((assets/'plans.json').read_text());selected=os.environ.get('SELECTED_PR','all')
if selected=='last-two':plans=[p for p in plans if p['number']>=5078]
elif selected=='remaining':plans=[p for p in plans if p['number']>=5074]
elif selected!='all':plans=[p for p in plans if str(p['number'])==selected]
assert plans
base='6f892bc5026efc6355d017ec38dfffb84c228100'
def run(args, log=None, expected=0):
 r=subprocess.run(args,cwd=source,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 if log:log.write_text(r.stdout)
 print(r.stdout,flush=True)
 if r.returncode!=expected:raise RuntimeError(f'{args}: exit {r.returncode}, expected {expected}')
 return r.stdout.strip()
def git_raw(*args):
 r=subprocess.run(['git',*args],cwd=source,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 if r.returncode:raise RuntimeError(r.stdout)
 return r.stdout
def git(*args):return git_raw(*args).strip()
def receipt(path):
 tree=git('write-tree');items=[]
 for name in git('ls-files').splitlines():
  f=source/name
  if f.is_file():items.append({'path':name,'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'bytes':f.stat().st_size})
 path.write_text(json.dumps({'original_head':git('rev-parse','HEAD'),'index_tree':tree,'files':items},indent=2)+'\n');return tree
# Deliberate test mutations prove the two reviewer coverage requests catch regressions.
def mutate_coverage(number):
 if number==5067:
  f=source/'apps/api/src/scraper/scrapeURL/lib/abortManager.ts';s=f.read_text();assert s.count('this.inner = inner;')==1;f.write_text(s.replace('this.inner = inner;','this.inner = inner instanceof Error ? new Error(inner.message) : inner;'))
 elif number==5074:
  for relative in ['helpers/source-tracker.ts','fire-0/helpers/source-tracker-f0.ts']:
   f=source/'apps/api/src/lib/extract'/relative;s=f.read_text();needle='return item === originalItem;';assert s.count(needle)==1;f.write_text(s.replace(needle,'return typeof item !== "string" && item === originalItem;'))
def native(plan,out,label):
 xml=out/f'{label}.xml';args=['timeout','-k','45s','15m','pnpm','harness','pnpm','exec','vitest','run',*plan['tests'],'--maxWorkers=1','--reporter=default','--reporter=junit',f'--outputFile.junit={xml}']
 r=subprocess.run(args,cwd=source/'apps/api',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);(out/f'{label}.log').write_text(r.stdout);print(r.stdout,flush=True)
 if not xml.exists():raise RuntimeError(f'{label}: no native test report, exit {r.returncode}')
 cases=list(ET.parse(xml).iter('testcase'));failed=[x for x in cases if x.find('failure') is not None or x.find('error') is not None];skipped=[x for x in cases if x.find('skipped') is not None]
 (out/f'{label}-result.json').write_text(json.dumps({'exit':r.returncode,'tests':len(cases),'failures':len(failed),'skips':len(skipped),'failing_tests':[x.get('name') for x in failed]},indent=2)+'\n')
 assert cases and not skipped
 if label=='before':
  assert r.returncode!=0 and failed, 'Original production or coverage mutant did not fail the new regression'
  expected=plan['expected_regression'];matched=[]
  for case in failed:
   failure=case.find('failure') if case.find('failure') is not None else case.find('error')
   reason=(failure.get('message') or '')+' '+(failure.text or '')
   if re.search(expected['name_pattern'],case.get('name') or '') and re.search(expected['reason_pattern'],reason,re.S):matched.append(case.get('name'))
  assert matched, 'Before failure did not match the expected named source regression and reason'
  (out/'before-causal-match.json').write_text(json.dumps({'expected':expected,'matched_tests':matched},indent=2)+'\n')
 else:assert r.returncode==0 and not failed, 'Candidate native suite failed'
for plan in plans:
 number=plan['number'];out=proof/str(number);out.mkdir(exist_ok=True);patch=assets/'repair.patch';assert hashlib.sha256(patch.read_bytes()).hexdigest()==plan['patch_sha256']
 git('reset','--hard',plan['head']);assert git('rev-parse','HEAD')==plan['head'];assert git('merge-base',base,'HEAD')==base
 git('apply','--check',str(patch));git('apply',str(patch))
 assert git('rev-parse','HEAD:apps/api/native')==plan['native_tree']
 # Preserve the actual upstream hook order; neither failure is suppressed.
 r=subprocess.run(['pnpm','knip','--cache'],cwd=source/'apps/api',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);(out/'knip-before-format.log').write_text(r.stdout);print(r.stdout,flush=True);assert r.returncode==0
 # Stage the complete authored API change so the real hook formats its files.
 git('read-tree',base);git('add','-A','--','apps/api')
 r=subprocess.run(['pnpm','lint-staged'],cwd=source/'apps/api',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);(out/'lint-staged.log').write_text(r.stdout);print(r.stdout,flush=True);assert r.returncode==0
 git('diff','--exit-code');tree=receipt(out/'candidate-manifest.json')
 (out/'final-candidate.patch').write_text(git_raw('diff','--cached',plan['head']));(out/'full-authored.patch').write_text(git_raw('diff','--cached',base))
 changed=git('diff','--name-only','--cached',plan['head']).splitlines();assert set(changed)<=set(plan['allowed_files']),f'Unreviewed formatter/source paths: {changed}'
 r=subprocess.run(['pnpm','knip','--cache'],cwd=source/'apps/api',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);(out/'knip.log').write_text(r.stdout);print(r.stdout,flush=True);assert r.returncode==0
 run(['timeout','-k','30s','10m','pnpm','--dir','apps/api','exec','tsc','--noEmit'],out/'tsc.log')
 assert git('write-tree')==tree;git('diff','--exit-code')
 # Keep final candidate tests, restore original production files. Coverage-only
 # requests use an explicitly labelled mutant instead of claiming a prior bug.
 for name in changed:
  if not name.endswith('.test.ts'):
   (source/name).write_bytes(subprocess.check_output(['git','show',f"{plan['head']}:{name}"],cwd=source))
 mutate_coverage(number);receipt(out/'before-manifest.json');native(plan,out,'before')
 git('checkout-index','--all','--force');assert git('write-tree')==tree;git('diff','--exit-code');native(plan,out,'after')
 assert git('rev-parse','HEAD')==plan['head'];assert git('write-tree')==tree;git('diff','--exit-code')
 receipt(out/'final-manifest.json');(out/'complete.json').write_text(json.dumps({'number':number,'original':plan['head'],'tested_index_tree':tree,'knip':'PASS','lint_staged':'PASS','source_commit_created':False,'before_kind':'deliberate coverage mutant' if number in [5067,5074] else 'original production with new regression tests','candidate':'native PASS; needs independent final formatted diff review before signing'},indent=2)+'\n')
