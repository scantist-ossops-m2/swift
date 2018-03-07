# Copyright (c) 2017 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from collections import defaultdict

from swift.common import utils
from swift.common.db_replicator import roundrobin_datadirs
from swift.common.ring import ring
from swift.common.utils import Timestamp
from swift.container.backend import ContainerBroker, DATADIR

TAB = '    '


def broker_key(broker):
    broker.get_info()
    return '%s/%s' % (broker.account, broker.container)


def container_type(broker):
    return 'ROOT' if broker.is_root_container() else 'SHARD'


def collect_brokers(conf_path, names2nodes):
    conf = utils.readconf(conf_path, 'container-replicator')
    root = conf.get('devices', '/srv/node')
    swift_dir = conf.get('swift_dir', '/etc/swift')
    c_ring = ring.Ring(swift_dir, ring_name='container')
    dirs = []
    brokers = defaultdict(dict)
    for node in c_ring.devs:
        datadir = os.path.join(root, node['device'], DATADIR)
        if os.path.isdir(datadir):
            dirs.append((datadir, node['id']))
    for part, object_file, node_id in roundrobin_datadirs(dirs):
        broker = ContainerBroker(object_file)
        names2nodes[broker_key(broker)][node_id] = broker
    return brokers


def print_broker_info(node, broker, indent_level=0):
    indent = indent_level * TAB
    info = broker.get_info()
    deleted_at = float(info['delete_timestamp'])
    if deleted_at:
        deleted_at = Timestamp(info['delete_timestamp']).isoformat
    else:
        deleted_at = ' - '
    print('%s%s, objs: %s, bytes: %s, put: %s, deleted: %s (%s)' %
          (indent, broker.get_db_state_text(), info['object_count'],
           info['bytes_used'], Timestamp(info['put_timestamp']).isoformat,
           deleted_at, node))


def print_db(node, broker, expect_type='ROOT', indent_level=0):
    indent = indent_level * TAB
    print('%s%s (%s)' % (indent, broker.db_file, node))
    actual_type = container_type(broker)
    if actual_type != expect_type:
        print('%s        ERROR expected %s but found %s' %
              (indent, expect_type, actual_type))


def print_shard_range(node, sr, indent_level):
    indent = indent_level * TAB
    range = '%r - %r' % (sr.lower, sr.upper)
    print('%s%23s, objs: %3s, bytes: %3s, timestamp: %s (%s), '
          'modified: %s (%s), %7s: %s (%s), deleted: %s (%s) %s' %
          (indent, range, sr.object_count, sr.bytes_used,
           Timestamp(sr.timestamp).isoformat, sr.timestamp.internal,
           Timestamp(sr.meta_timestamp).isoformat, sr.meta_timestamp.internal,
           sr.state_text, sr.state_timestamp.isoformat,
           sr.state_timestamp.internal, sr.deleted, node, sr.name))


def print_shard_range_info(node, shard_ranges, indent_level=0):
    shard_ranges.sort(key=lambda x: x.deleted)
    for sr in shard_ranges:
        print_shard_range(node, sr, indent_level)


def print_sharding_info(node, broker, indent_level=0):
    indent = indent_level * TAB
    print('%s%s (%s)' % (indent, broker.get_sharding_info(), node))


def print_container(name, name2nodes2brokers, expect_type='ROOT',
                    indent_level=0, used_names=None):
    used_names = used_names or set()
    indent = indent_level * TAB
    node2broker = name2nodes2brokers[name]
    print('%sName: %s' % (indent, name))
    if name in used_names:
        print('%s  (Details already listed)\n' % indent)
        return

    used_names.add(name)
    print(indent + 'DB files:')
    for node, broker in node2broker.items():
        print_db(node, broker, expect_type, indent_level=indent_level + 1)

    print(indent + 'Info:')
    for node, broker in node2broker.items():
        print_broker_info(node, broker, indent_level=indent_level + 1)

    print(indent + 'Sharding info:')
    for node, broker in node2broker.items():
        print_sharding_info(node, broker, indent_level=indent_level + 1)
    print(indent + 'Own shard range:')
    for node, broker in node2broker.items():
        shard_ranges = broker.get_shard_ranges(
            include_deleted=True, include_own=True, exclude_others=True)
        print_shard_range_info(node, shard_ranges,
                               indent_level=indent_level + 1)
    print(indent + 'Shard ranges:')
    shard_names = set()
    for node, broker in node2broker.items():
        shard_ranges = broker.get_shard_ranges(include_deleted=True)
        for sr_name in shard_ranges:
            shard_names.add(sr_name.name)
        print_shard_range_info(node, shard_ranges,
                               indent_level=indent_level + 1)
    print(indent + 'Shards:')
    for sr_name in shard_names:
        print_container(sr_name, name2nodes2brokers, expect_type='SHARD',
                        indent_level=indent_level + 1, used_names=used_names)
    print('\n')


def run(conf_paths):
    name2nodes2brokers = defaultdict(dict)
    for conf_path in conf_paths:
        collect_brokers(conf_path, name2nodes2brokers)

    for name, node2broker in name2nodes2brokers.items():
        expect_root = False
        for node, broker in node2broker.items():
            expect_root = broker.is_root_container() or expect_root
        if expect_root:
            print_container(name, name2nodes2brokers)


if __name__ == '__main__':
    conf_dir = '/etc/swift/container-server'
    conf_paths = [os.path.join(conf_dir, p) for p in os.listdir(conf_dir)
                  if p.endswith(('conf', 'conf.d'))]
    run(conf_paths)
