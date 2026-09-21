#!/usr/bin/env python3
"""Run real review/paragraph paths on frozen text fixtures, never live recordings."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from web.backend import engine
from web.backend.review import compose_text
from web.backend.transcription import Options


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path)
    parser.add_argument('--url',default='http://127.0.0.1:11435')
    parser.add_argument("--models", nargs="+", default=["qwen3:8b-q4_K_M", "qwen3.5:9b"])
    args=parser.parse_args()
    engine.OLLAMA_URL=args.url+'/api/chat'
    cases=json.loads((args.folder/'cases.json').read_text())
    original=requests.post
    for model in args.models:
        dest=args.folder/model.replace(':','-');dest.mkdir(exist_ok=True)
        try:
            for case in cases:
                for formatted in ([True,False] if case['kind']=='injected' else [True]):
                    name=case['id']+('-combined' if formatted else '-edits')
                    path=dest/(name+'.json')
                    if path.exists():continue
                    attempts=[]
                    def capture(url,**kwargs):
                        start=time.monotonic()
                        response=original(url,**kwargs)
                        attempts.append({'seconds':time.monotonic()-start,'request':kwargs['json'],'body':response.json(),'status':response.status_code})
                        return response
                    options=Options(language=case['language'],course_name=case['course'],format_text=formatted,llm_model=model,review_context=json.dumps(case.get('context',{}),ensure_ascii=False))
                    start=time.monotonic();requests.post=capture
                    try:
                        total,fallback,suggestions,breaks,stats=engine.Transcriber._review_items(case['items'],options,None,case.get('confidence'))
                        text=compose_text(case['items'],breaks,formatted)
                        result={'batches':total,'fallback':fallback,'suggestions':[s.public() for s in suggestions],'breaks':sorted(breaks),'stats':asdict(stats),'text':text,'preserved':re.sub(r'\s+','',text)==re.sub(r'\s+','',''.join(case['items']))}
                    except Exception as error:result={'error':str(error)}
                    finally:requests.post=original
                    result.update(model=model,case=name,seconds=time.monotonic()-start,attempts=attempts)
                    path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
                    print(json.dumps({k:v for k,v in result.items() if k in ['model','case','seconds','fallback','error','breaks','suggestions','preserved']},ensure_ascii=False),flush=True)
        finally:
            original(args.url+'/api/generate',json={'model':model,'keep_alive':0},timeout=120).raise_for_status()

if __name__=='__main__':main()
