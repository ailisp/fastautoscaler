from gevent.pywsgi import WSGIServer
import yaml
from flask import Flask, jsonify, request, escape, abort
import rc
from rc import run, pmap
from queue import Queue
import threading
import uuid
import hashlib
from sqlitedict import SqliteDict
import datetime
import sys
from pytimeparse.timeparse import timeparse
import atexit

app = Flask(__name__)

config = yaml.load(open('config.yml'))
machine_groups = {}
for mg in config['machine_groups']:
    machine_groups[mg['name']] = mg

machines = SqliteDict('./machines.sqlite', autocommit=True)
print(list(machines.iterkeys()))

task_queue = Queue()


def get_machines_in_group(group_name):
    ret = []
    for _, m in machines.iteritems():
        if m['machine_group']['name'] == group_name:
            ret.append(m)
    return ret


def create_machine(item):
    mg = item.get('machine_group')
    max_machines = mg.get('max')
    name = item['machine_name']
    if max_machines:
        machine_in_group = get_machines_in_group(mg['name'])
        if len(machine_in_group) >= max_machines:
            task_queue.put(item)
            print(
                f'Already have {max_machines} machines for group {mg["name"]}, wait until there is space')
            return
    machines[name] = {'status': 'creating', **item}
    try:
        print(f'Creating machine {name}')
        getattr(rc, mg['provider']).create(
            name=name, **mg['spec'])
        created_at = datetime.datetime.utcnow()
        init_script = item.get('init_script')
        if init_script:
            machines[name] = {
                'status': 'initializing', **item
            }
            p = getattr(rc, mg['provider']).get(name).run(
                'bash', input=f'set -euo pipefail\n{init_script}')
            if p.returncode != 0:
                raise Exception(p.stderr)
        machines[name] = {
            'status': 'running', **item, 'created_at': datetime.datetime.utcnow().isoformat() + created_at.isoformat() + 'Z'}
        timeout = mg.get('timeout')
        if type(timeout) is str:
            timeout = timeparse(timeout)
        if timeout:
            threading.Timer(timeout, delete_machine, [name]).start()

        print(f'Success creating machine: {name}')
    except:
        print(f'Error creating machine: {name}')
        print(sys.exc_info())
        try:
            del machines[name]
        except KeyError:
            pass


def delete_machine(name):
    if machines.get(name):
        machine = machines[name]
        machine['status'] = 'deleting'
        machines[name] = machine

        print(f'Deleting machine {name}')
        provider = getattr(rc, machine['machine_group']['provider'])
        try:
            provider.get(name).delete()
            print(f'Success deleting machine: {name}')
        except:
            print(f'Warning: Failed to delete machine {name}')
            print(sys.exc_info())
        finally:
            try:
                del machines[name]
            except KeyError:
                pass


def worker():
    while True:
        item = task_queue.get()
        if item is None:
            break
        try:
            if item['type'] == 'create':
                item.pop('type')
                create_machine(item)
            elif item['type'] == 'delete':
                delete_machine(item['machine_name'])
        except:
            print(sys.exc_info())
        finally:
            task_queue.task_done()


num_worker_threads = 10
threads = []
for i in range(num_worker_threads):
    t = threading.Thread(target=worker, daemon=False)
    t.start()
    threads.append(t)


def new_machine_name(prefix):
    return prefix + '-' + hashlib.md5(str(uuid.uuid4()).encode('utf-8')).hexdigest()


@app.route('/', methods=['GET', 'HEAD'])
def status():
    return jsonify(None)


@app.route('/machines', methods=['GET'])
def list_machines():
    return jsonify(list(machines.iteritems()))


@app.route('/machines/<name>', methods=['GET'])
def get_machine(name):
    machine = machines.get(name)
    if machine:
        return jsonify(machines.get(name))
    else:
        abort(404)


@app.route('/machines', methods=['POST'])
def request_machine():
    data = request.get_json()
    name = data.get("group_name")
    init_script = data.get("init_script")

    mg = machine_groups[name]
    provider = mg['provider']
    machine_name = new_machine_name(name)
    task_queue.put({"type": "create",
                    "machine_name": machine_name,
                    "init_script": init_script,
                    "machine_group": mg})
    return jsonify({"machine_name": machine_name})


@app.route('/machines/<name>', methods=['DELETE'])
def release_machine(name):
    task_queue.put({"type": "delete",
                    "machine_name": name})
    return jsonify(None)


def atexit_cleanup():
    print('Drain task_queue')
    try:
        while task_queue.get(block=False):
            task_queue.task_done()
    except:
        pass

    print('Shutdown worker threads')
    for _ in range(num_worker_threads):
        task_queue.put(None)

    pmap(delete_machine, machines.keys())


atexit.register(atexit_cleanup)

http_server = WSGIServer(('', 5000), app)

try:
    http_server.serve_forever()
except KeyboardInterrupt:
    pass
