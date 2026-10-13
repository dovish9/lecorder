#!/usr/bin/env python3
"""Isolated local comparison. Ollama is used ONLY as a historical benchmark baseline.

Usage: python scripts/benchmark_ai.py CASE_JSON OUTPUT_DIRECTORY
CASE_JSON contains text and image (PNG path). Does not change production settings.
Measures wall time and sampled process-tree RSS (not total Metal allocation).
"""
import argparse,base64,json,os,subprocess,sys,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
from web.backend.ai_settings import defaults
from web.backend.ai_runtime import close_all,runtime_for
from web.backend.ai_providers import generate


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('case',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args()
    case=json.loads(args.case.read_text());args.output.mkdir(parents=True,exist_ok=True)
    image=base64.b64encode(Path(case['image']).read_bytes()).decode()
    messages=[{'role':'user','content':'이 페이지의 핵심 개념과 공식을 한국어 Markdown으로 설명하세요. 수식은 $...$로 표시하고 원문에 없는 내용은 만들지 마세요. 400자 이내로 완결하세요.\n'+case['text'],'images':[image]}]
    schema={'type':'object','properties':{'markdown':{'type':'string'}},'required':['markdown'],'additionalProperties':False}
    results=[]
    for provider in ('llama_cpp','ollama_baseline'):
        process=None;stop=threading.Event();peak=[0];cfg=defaults()['llm'];cfg['base_url']='http://127.0.0.1:18083'
        if provider=='ollama_baseline':
            import socket
            with socket.socket() as test:test.bind(('127.0.0.1',11435))
            process=subprocess.Popen(['ollama','serve'],env={**os.environ,'OLLAMA_HOST':'127.0.0.1:11435'},stdout=(args.output/'ollama.log').open('w'),stderr=subprocess.STDOUT)
            for _ in range(100):
                try:
                    if requests.get('http://127.0.0.1:11435/api/tags',timeout=.5).ok:break
                except requests.RequestException:time.sleep(.2)
            else:raise RuntimeError('Ollama benchmark server failed')
        def sample():
            while not stop.wait(.5):
                parent=process.pid if process else getattr(runtime_for(cfg)._process,'pid',None)
                if not parent:continue
                rows=[list(map(int,line.split())) for line in subprocess.check_output(['ps','-axo','pid=,ppid=,rss='],text=True).splitlines()]
                selected={parent}
                for _ in range(3):selected|={pid for pid,ppid,rss in rows if ppid in selected}
                peak[0]=max(peak[0],sum(rss for pid,ppid,rss in rows if pid in selected)*1024)
        sampler=threading.Thread(target=sample,daemon=True);sampler.start()
        try:
            for run in ('cold','warm'):
                start=time.monotonic()
                if provider=='llama_cpp':
                    result=generate(messages,schema,config=cfg,api_key='',max_tokens=512,temperature=0)
                else:
                    body=requests.post('http://127.0.0.1:11435/api/chat',json={'model':'qwen3.5:9b','messages':messages,'format':schema,'think':False,'stream':False,'keep_alive':'5m','options':{'num_ctx':16384,'num_predict':512,'temperature':0}},timeout=600)
                    body.raise_for_status();body=body.json();result={'result':json.loads(body['message']['content']),'input_tokens':body.get('prompt_eval_count'),'output_tokens':body.get('eval_count'),'done_reason':body.get('done_reason')}
                entry={'provider':provider,'run':run,'seconds':time.monotonic()-start,'peak_process_tree_rss_bytes':peak[0],**result};results.append(entry)
                (args.output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(provider,run,round(entry['seconds'],2),flush=True)
        finally:
            stop.set();sampler.join();close_all()
            if process:
                try:requests.post('http://127.0.0.1:11435/api/generate',json={'model':'qwen3.5:9b','keep_alive':0},timeout=30)
                finally:process.terminate();process.wait(timeout=15)
if __name__=='__main__':main()
