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
import traceback

app = Flask(__name__)

config = yaml.load(open('config.yml'))
machine_groups = {}
for mg in config['machine_groups']:
    machine_groups[mg['name']] = mg

machines = SqliteDict('./machines.sqlite', autocommit=True)
print(list(machines.iterkeys()))

task_queue = Queue()

timeout_threads = {}


def get_machines_in_group(group_name):
    ret = []
    for _, m in machines.iteritems():
        if m['machine_group']['name'] == group_name:
            ret.append(m)
    return ret


def get_machine_name_by_ip(ip):
    for name in machines.keys():
        print(machines[name])
        if machines[name]['ip'] == ip:
            return name


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
    machines[name] = {'status': 'creating', **item, 'ip':''}
    try:
        print(f'Creating machine {name}')
        machine_obj = getattr(rc, mg['provider']).create(
            name=name, **mg['spec'])
        created_at = datetime.datetime.utcnow()
        init_script = item.get('init_script')
        if init_script:
            machines[name] = {
                'status': 'initializing', **item
            }
            p = getattr(rc, mg['provider']).get(name).bash(f'set -euo pipefail\n{init_script}')
            if p.returncode != 0:
                raise Exception(p.stderr)
        machines[name] = {
            'status': 'running', **item, 'created_at': datetime.datetime.utcnow().isoformat() + created_at.isoformat() + 'Z',
            'ip': machine_obj.ip}
        timeout = mg.get('timeout')
        if type(timeout) is str:
            timeout = timeparse(timeout)
        if timeout:
            t = threading.Timer(timeout, delete_machine, [
                name, f'Deleting machine {name} due to timeout'])
            timeout_threads[name] = t
            t.start()

        print(f'Success creating machine: {name}')
    except:
        print(f'Error creating machine: {name}')
        traceback.print_exc()
        try:
            del machines[name]
        except KeyError:
            pass


def delete_machine(name, msg=None):
    if msg is None:
        msg = f'Deleting machine {name}...'
    if machines.get(name):
        machine = machines[name]
        machine['status'] = 'deleting'
        machines[name] = machine

        print(msg)
        provider = getattr(rc, machine['machine_group']['provider'])
        try:
            m = provider.get(name)
            if m:
                m.delete()
            print(f'Success deleting machine: {name}')
        except:
            m = provider.get(name)
            if m:
                print(f'Warning: Failed to delete machine {name}')
                traceback.print_exc()
        finally:
            try:
                del machines[name]
                print(f'delete {name} from sqlite db')
            except KeyError:
                pass
    try:
        del timeout_threads[name]
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
            elif item['type'] == 'delete_by_ip':
                name = get_machine_name_by_ip(item['ip'])
                delete_machine(name)
        except:
            traceback.print_exc()
        finally:
            task_queue.task_done()


num_worker_threads = 20
threads = []
for i in range(num_worker_threads):
    t = threading.Thread(target=worker, daemon=True)
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


@app.route('/machines/ip/<ip>', methods=['DELETE'])
def release_machine_by_ip(ip):
    task_queue.put({"type": "delete_by_ip",
                    "ip": ip})
    return jsonify(None)


def atexit_cleanup():
    print('Drain task_queue')
    for _, t in timeout_threads.items():
        t.cancel()
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
