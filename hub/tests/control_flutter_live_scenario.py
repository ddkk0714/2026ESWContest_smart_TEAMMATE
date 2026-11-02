"""Explicit local-only integration driver, not a product broker or service.

Existing atlas-dev Flutter connects over a loopback-only byte pipe to the real
Python test broker. No LAN listener, board access, service change or deployment.
Synthetic FSM time is mapped to wall time only in display envelope timestamps;
server timers and sensor simulation are unchanged.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import selectors
import socket
import subprocess
import tempfile
import threading
import time

from test_control_platform_e2e import Simulation, STATE, REQUEST
from test_mqtt_reconnect import _wait

CONTAINER = 'deskmate-atlas-atlas-dev-1'
APP = '/tmp/deskmate-ui-validation/display/atlas/ui_env_app'
# TCP remains loopback-only on both sides. Docker stdin/stdout transports raw
# bytes; no privileged networking, port publication or container recreation.
BRIDGE = r'''
import json,os,selectors,socket,sys
server=socket.socket(); server.bind(('127.0.0.1',0)); server.listen(1)
print(json.dumps({'port':server.getsockname()[1]}),flush=True)
conn,_=server.accept(); selector=selectors.DefaultSelector()
selector.register(conn,selectors.EVENT_READ); selector.register(0,selectors.EVENT_READ)
try:
 while True:
  for key,_ in selector.select(1):
   if key.fileobj is conn:
    data=conn.recv(65536)
    if not data: raise EOFError()
    sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
   else:
    data=os.read(0,65536)
    if not data: raise EOFError()
    conn.sendall(data)
except (EOFError,OSError): pass
finally: conn.close();server.close()
'''


def main():
    # Refuse paths other than the explicit temporary test copy.
    assert APP.startswith('/tmp/')
    with tempfile.TemporaryDirectory(prefix='deskmate-flutter-live-') as directory:
        simulation = Simulation(Path(directory))
        pipe = frontend = sock = None
        try:
            simulation.hub.boot_id = 'mock-live-ui'
            simulation.start_focus()
            proposal_state, _ = simulation.proposal(simulation.low_light)
            proposal = copy.deepcopy(simulation.hub.pending_request)
            planned = proposal_state['data']['sensor_summary']['control']['commands'][0]
            pipe = subprocess.Popen(['docker','exec','-i',CONTAINER,'python3','-u','-c',BRIDGE],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            bridge_port = json.loads(pipe.stdout.readline())['port']
            sock = socket.create_connection(('127.0.0.1',simulation.port), timeout=5)
            sock.settimeout(None)
            def forward():
                selector = selectors.DefaultSelector()
                selector.register(sock,selectors.EVENT_READ)
                selector.register(pipe.stdout,selectors.EVENT_READ)
                try:
                    while True:
                        for key,_ in selector.select(1):
                            if key.fileobj is sock:
                                data=sock.recv(65536)
                                if not data: return
                                pipe.stdin.write(data); pipe.stdin.flush()
                            else:
                                data=os.read(pipe.stdout.fileno(),65536)
                                if not data: return
                                sock.sendall(data)
                except (OSError,ValueError):
                    pass
                finally:
                    selector.close()
                    try: pipe.stdin.close()
                    except (OSError, ValueError): pass
            threading.Thread(target=forward,daemon=True).start()
            log_path=Path('/tmp/deskmate-control-flutter-live.log')
            with log_path.open('w') as log:
                frontend=subprocess.Popen(['docker','exec','-w',APP,CONTAINER,'flutter','test',
                    'test/control_live_mqtt.dart','--reporter','expanded',
                    f'--dart-define=DESKMATE_MOCK_BRIDGE_PORT={bridge_port}'],stdout=log,stderr=subprocess.STDOUT)
                deadline=time.monotonic()+90
                current=proposal_state
                handled=False
                while frontend.poll() is None and time.monotonic()<deadline:
                    if simulation.cache.snapshot().feedback and not handled:
                        current=simulation.step(simulation.low_light)
                        assert _wait(lambda:not simulation.gateway._queue.empty())
                        asyncio.run(simulation.gateway.pump())
                        assert _wait(lambda:any(result.get('status') == 'succeeded'
                            for result in list(simulation.cache.control_results)))
                        current=simulation.step(simulation.low_light)
                        handled=True
                    def publish(topic,envelope):
                        value=copy.deepcopy(envelope)
                        value['ts']=time.time()  # presentation clock only; all plan/ID fields unchanged
                        simulation.hub_client.publish(topic,json.dumps(value),qos=1)
                    publish(STATE,current)
                    if not handled: publish(REQUEST,proposal)
                    time.sleep(.10)
                if frontend.poll() is None:
                    frontend.terminate(); frontend.wait(10)
                    raise AssertionError('Flutter local integration timed out')
            assert frontend.returncode==0, log_path.read_text()
            assert handled and len(simulation.commands)==1 and len(simulation.adapter.calls)==1
            assert simulation.commands[0]['command_id']==planned['command_id']
            result=current['data']['sensor_summary']['control']['last_episode']['commands'][0]
            assert current['data']['fsm_state']=='MONITOR'
            assert result['status']=='succeeded' and result['state_confirmed']
            print(json.dumps({'result':'passed','mode':'mock','path':'real Hub/FSM -> MQTT -> Flutter Widget -> feedback -> Dispatcher -> Mock Gateway -> result -> Flutter',
                'target':result['target'],'operation':result['op'],'value':result['value'],
                'adapter_calls':len(simulation.adapter.calls),'state_confirmed':result['state_confirmed'],
                'hardware':False,'log':str(log_path)},indent=2))
        finally:
            if frontend is not None and frontend.poll() is None:
                frontend.terminate(); frontend.wait(10)
            if sock is not None: sock.close()
            if pipe is not None:
                pipe.terminate(); pipe.wait(10)
            simulation.close()

if __name__=='__main__':
    main()
