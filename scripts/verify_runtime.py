#!/usr/bin/env python3
"""Actual local-model lifecycle checks. Requires installed local models and a WAV sample."""
import argparse
import json
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web.backend.ai_settings import defaults, using
from web.backend.ai_providers import generate
from web.backend.ai_runtime import runtime_for, ModelRuntime
from web.backend.speech.engine import Transcriber
from web.backend.inference import inference
from web.backend.services import Services
from web.backend.execution_context import start_thread
from web.backend.speech.transcription import CancellationToken, Options, TranscriptionCancelled


def verify(source, services, config):
    report={}
    speech=runtime_for(config['speech'])
    assert speech.state == 'idle' and speech._process is None
    token=CancellationToken()
    with inference.lease(speech=True), speech.session(token):
        pid=speech._process.pid
        with speech.session():
            assert speech._process.pid == pid
        errors=[]
        def transcribe():
            try: Transcriber._whisper(source,Options(language='en'),None,cancellation=token)
            except Exception as error: errors.append(error)
        thread=start_thread(transcribe,'live-cancel-test')
        deadline=time.monotonic()+5
        while not token._processes and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.05)
        token.cancel();thread.join(5)
        assert not thread.is_alive() and errors and isinstance(errors[0],TranscriptionCancelled), errors
    assert speech._process is None
    report['speech']={'lazy':True,'reuse_pid':pid,'request_cancel_reaped':True}
    with speech.session():
        pid=speech._process.pid
    time.sleep(config['speech']['idle_seconds']+1)
    assert speech._process is None and speech.state=='idle'
    report['speech']['idle_released_pid']=pid
    schema={'type':'object','properties':{'answer':{'type':'integer'}},'required':['answer'],'additionalProperties':False}
    answer=generate([{'role':'user','content':'2+3?'}],schema,max_tokens=32)
    assert answer['result']['answer']==5
    llm=runtime_for(config['llm']);pid=llm._process.pid
    again=generate([{'role':'user','content':'2+3?'}],schema,max_tokens=32)
    assert again['result']['answer']==5 and llm._process.pid==pid
    report['llm']={'reuse_pid':pid,'first_seconds':answer['seconds'],'next_seconds':again['seconds']}
    with inference.lease(speech=True), speech.session():
        assert llm._process is None, 'Inactive LLM must be released when speech loads'
        report['model_switch_released_llm']=True
        close_process=speech._process
    services.runtimes.close()
    assert close_process.poll() is not None
    report['owned_process_closed']=True
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        conflict=ModelRuntime({**config['speech'],'base_url':f'http://127.0.0.1:{listener.getsockname()[1]}'},log_dir=services.runtimes.log_dir)
        try:
            with conflict.session(): raise AssertionError('Busy port accepted')
        except RuntimeError as error:
            assert '다른 서버' in str(error)
        finally: conflict.close()
        assert listener.fileno() >= 0
    report['port_conflict_preserved_external_listener']=True
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not args.source.is_file(): parser.error('A real WAV sample is required.')
    config=defaults()
    for area,port in [('speech',18080),('llm',18081)]:
        config[area].update(base_url=f'http://127.0.0.1:{port}',idle_seconds=2)
    with tempfile.TemporaryDirectory(prefix='lecorder-runtime-') as temporary:
        services=Services.create(temporary)
        try:
            with services.scope(),using(config): report=verify(args.source,services,config)
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2))
        finally: services.close()


if __name__=='__main__': main()
