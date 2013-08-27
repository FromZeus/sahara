# Copyright (c) 2013 Mirantis Inc.
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


import socket
import telnetlib
import time


import savannaclient.api.client as savanna_client
import unittest2


from savanna.openstack.common import excutils
from savanna.tests.integration_new.configs.config import ITConfig as cfg
from savanna.utils import remote


def skip_test(config_name, message=''):

    def handle(func):

        def call(self, *args, **kwargs):

            if getattr(self, config_name):

                print('======================================================')
                print(message)
                print('======================================================')

            else:

                return func(self, *args, **kwargs)

        return call

    return handle


class ITestCase(unittest2.TestCase):

    def setUp(self):

        self.COMMON = cfg().COMMON
        self.VANILLA = cfg().VANILLA
        self.HDP = cfg().HDP

        telnetlib.Telnet(self.COMMON.SAVANNA_HOST,
                         self.COMMON.SAVANNA_PORT)

        self.savanna = savanna_client.Client(
            username=self.COMMON.OS_USERNAME,
            api_key=self.COMMON.OS_PASSWORD,
            project_name=self.COMMON.OS_TENANT_NAME,
            auth_url=self.COMMON.OS_AUTH_URL,
            savanna_url='http://%s:%s/%s' % (
                self.COMMON.SAVANNA_HOST,
                self.COMMON.SAVANNA_PORT,
                self.COMMON.SAVANNA_API_VERSION))

#-------------------------Methods for object creation--------------------------

    def create_node_group_template(self, name, plugin, description,
                                   volumes_per_node, volume_size,
                                   node_processes, node_configs,
                                   hadoop_version=None):

        if not hadoop_version:

            hadoop_version = plugin.HADOOP_VERSION

        data = self.savanna.node_group_templates.create(
            name, plugin.PLUGIN_NAME, hadoop_version,
            self.COMMON.FLAVOR_ID, description, volumes_per_node,
            volume_size, node_processes, node_configs)

        node_group_template_id = data.id

        return node_group_template_id

    def create_cluster_template(self, name, plugin, description,
                                cluster_configs, node_groups, anti_affinity,
                                hadoop_version=None):

        if not hadoop_version:

            hadoop_version = plugin.HADOOP_VERSION

        data = self.savanna.cluster_templates.create(
            name, plugin.PLUGIN_NAME, hadoop_version, description,
            cluster_configs, node_groups, anti_affinity)

        cluster_template_id = data.id

        return cluster_template_id

    def create_cluster_and_get_info(self, plugin, cluster_template_id,
                                    description, cluster_configs, node_groups,
                                    anti_affinity, hadoop_version=None,
                                    image_id=None):

        if not hadoop_version:

            hadoop_version = plugin.HADOOP_VERSION

        if not image_id:

            image_id = plugin.IMAGE_ID

        self.cluster_id = None

        data = self.savanna.clusters.create(
            self.COMMON.CLUSTER_NAME, plugin.PLUGIN_NAME, hadoop_version,
            cluster_template_id, image_id, description, cluster_configs,
            node_groups, self.COMMON.USER_KEYPAIR_ID, anti_affinity
        )

        self.cluster_id = data.id

        self.poll_cluster_state(self.cluster_id)

        node_ip_list_with_node_processes = \
            self.get_cluster_node_ip_list_with_node_processes(self.cluster_id)

        try:

            node_info = self.get_node_info(node_ip_list_with_node_processes,
                                           plugin)

        except Exception as e:

            with excutils.save_and_reraise_exception():

                self.savanna.clusters.delete(self.cluster_id)
                print(
                    'Failure during check of node process deployment '
                    'on cluster node: ' + str(e)
                )

        try:

            self.await_active_workers_for_namenode(node_info, plugin)

        except Exception as e:

            with excutils.save_and_reraise_exception():

                self.savanna.clusters.delete(self.cluster_id)
                print(
                    'Failure while active worker waiting for namenode: '
                    + str(e)
                )

        # For example: method "create_cluster_and_get_info" return
        # {
        #       'node_info': {
        #               'tasktracker_count': 3,
        #               'node_count': 6,
        #               'namenode_ip': '172.18.168.242',
        #               'datanode_count': 3
        #               },
        #       'cluster_id': 'bee5c6a1-411a-4e88-95fc-d1fbdff2bb9d',
        #       'node_ip_list': {
        #               '172.18.168.153': ['tasktracker', 'datanode'],
        #               '172.18.168.208': ['secondarynamenode'],
        #               '172.18.168.93': ['tasktracker'],
        #               '172.18.168.101': ['tasktracker', 'datanode'],
        #               '172.18.168.242': ['namenode', 'jobtracker'],
        #               '172.18.168.167': ['datanode']
        #       },
        #       'plugin_name': 'vanilla'
        # }

        return {
            'cluster_id': self.cluster_id,
            'node_ip_list': node_ip_list_with_node_processes,
            'node_info': node_info,
            'plugin': plugin
        }

#---------Helper methods for cluster info obtaining and its processing---------

    def poll_cluster_state(self, cluster_id):

        data = self.savanna.clusters.get(cluster_id)

        timeout = self.COMMON.CLUSTER_CREATION_TIMEOUT * 60
        while str(data.status) != 'Active':

            print('CLUSTER STATUS: ' + str(data.status))

            if str(data.status) == 'Error':

                print('\n' + str(data) + '\n')

                self.fail('Cluster state == \'Error\'.')

            if timeout <= 0:

                print('\n' + str(data) + '\n')

                self.fail(
                    'Cluster did not return to \'Active\' state '
                    'within %d minutes.' % self.COMMON.CLUSTER_CREATION_TIMEOUT
                )

            data = self.savanna.clusters.get(cluster_id)
            time.sleep(10)
            timeout -= 10

        return str(data.status)

    def get_cluster_node_ip_list_with_node_processes(self, cluster_id):

        data = self.savanna.clusters.get(cluster_id)
        node_groups = data.node_groups

        node_ip_list_with_node_processes = {}
        for node_group in node_groups:

            instances = node_group['instances']
            for instance in instances:

                node_ip = instance['management_ip']
                node_ip_list_with_node_processes[node_ip] = node_group[
                    'node_processes']

        # For example:
        # node_ip_list_with_node_processes = {
        #       '172.18.168.181': ['tasktracker'],
        #       '172.18.168.94': ['secondarynamenode'],
        #       '172.18.168.208': ['namenode', 'jobtracker'],
        #       '172.18.168.93': ['tasktracker', 'datanode'],
        #       '172.18.168.44': ['tasktracker', 'datanode'],
        #       '172.18.168.233': ['datanode']
        # }

        return node_ip_list_with_node_processes

    def try_telnet(self, host, port):

        try:

            telnetlib.Telnet(host, port)

        except Exception as e:

            with excutils.save_and_reraise_exception():

                print(
                    'Telnet has failed: ' + str(e) +
                    '  NODE IP: %s, PORT: %s. Passed %s minute(s).'
                    % (host, port, self.COMMON.TELNET_TIMEOUT)
                )

    def get_node_info(self, node_ip_list_with_node_processes, plugin):

        tasktracker_count = 0
        datanode_count = 0
        node_count = 0

        for node_ip, processes in node_ip_list_with_node_processes.items():

            self.try_telnet(node_ip, '22')
            node_count += 1

            for process in processes:

                if process in plugin.HADOOP_PROCESSES_WITH_PORTS:

                    for i in range(self.COMMON.TELNET_TIMEOUT * 60):

                        try:

                            time.sleep(1)
                            telnetlib.Telnet(
                                node_ip,
                                plugin.HADOOP_PROCESSES_WITH_PORTS[process]
                            )

                            break

                        except socket.error:

                            print(
                                'Connection attempt. NODE PROCESS: %s, '
                                'PORT: %s.'
                                % (process,
                                   plugin.HADOOP_PROCESSES_WITH_PORTS[process])
                            )

                    else:

                        self.try_telnet(
                            node_ip,
                            plugin.HADOOP_PROCESSES_WITH_PORTS[process]
                        )

            if plugin.PROCESS_NAMES['tt'] in processes:

                tasktracker_count += 1

            if plugin.PROCESS_NAMES['dn'] in processes:

                datanode_count += 1

            if plugin.PROCESS_NAMES['nn'] in processes:

                namenode_ip = node_ip

        return {
            'namenode_ip': namenode_ip,
            'tasktracker_count': tasktracker_count,
            'datanode_count': datanode_count,
            'node_count': node_count
        }

    def await_active_workers_for_namenode(self, node_info, plugin):

        self.open_ssh_connection(node_info['namenode_ip'],
                                 plugin.NODE_USERNAME)

        for i in range(self.COMMON.HDFS_INITIALIZATION_TIMEOUT * 6):

            time.sleep(10)

            active_tasktracker_count = self.execute_command(
                'sudo su -c "hadoop job -list-active-trackers" %s'
                % plugin.HADOOP_USER)[1]

            active_datanode_count = int(
                self.execute_command(
                    'sudo su -c "hadoop dfsadmin -report" %s \
                    | grep "Datanodes available:.*" | awk \'{print $3}\''
                    % plugin.HADOOP_USER)[1]
            )

            if not active_tasktracker_count:

                active_tasktracker_count = 0

            else:

                active_tasktracker_count = len(
                    active_tasktracker_count[:-1].split('\n')
                )

            if (
                    active_tasktracker_count == node_info['tasktracker_count']
            ) and (
                    active_datanode_count == node_info['datanode_count']
            ):

                break

        else:

            self.fail(
                'Tasktracker or datanode cannot be started within '
                '%s minute(s) for namenode.'
                % self.COMMON.HDFS_INITIALIZATION_TIMEOUT
            )

        self.close_ssh_connection()

#---------------------------------Remote---------------------------------------

    def open_ssh_connection(self, host, node_username):

        remote._connect(
            host, node_username, open(self.COMMON.PATH_TO_SSH_KEY).read()
        )

    def execute_command(self, cmd):

        return remote._execute_command(cmd, get_stderr=True)

    def write_file_to(self, remote_file, data):

        remote._write_file_to(remote_file, data)

    def read_file_from(self, remote_file):

        return remote._read_file_from(remote_file)

    def close_ssh_connection(self):

        remote._cleanup()

    def transfer_helper_script_to_node(self, script_name, parameter_list):

        script = open('integration_new/tests/helper_scripts/%s'
                      % script_name).read()

        if parameter_list:

            for parameter, value in parameter_list.iteritems():

                script = script.replace(
                    '%s=""' % parameter, '%s=%s' % (parameter, value))

        try:

            self.write_file_to('script.sh', script)

        except Exception as e:

            with excutils.save_and_reraise_exception():

                print(
                    'Failure while helper script transferring '
                    'to cluster node: ' + str(e)
                )

        self.execute_command('chmod 777 script.sh')

    def transfer_helper_script_to_nodes(self, node_ip_list, node_username,
                                        script_name, parameter_list=None):

        for node_ip in node_ip_list:

            self.open_ssh_connection(node_ip, node_username)

            self.transfer_helper_script_to_node(script_name, parameter_list)

            self.close_ssh_connection()

#--------------------------------Helper methods--------------------------------

    def delete_objects(self, cluster_id=None,
                       cluster_template_id=None,
                       node_group_template_id_list=None):

        if cluster_id:

            self.savanna.clusters.delete(cluster_id)

        if cluster_template_id:

            self.savanna.cluster_templates.delete(cluster_template_id)

        if node_group_template_id_list:

            for node_group_template_id in node_group_template_id_list:

                self.savanna.node_group_templates.delete(
                    node_group_template_id
                )

    def delete_swift_container(self, swift, container):

        objects = [obj['name'] for obj in swift.get_container(container)[1]]
        for obj in objects:

            swift.delete_object(container, obj)

        swift.delete_container(container)

    def print_error_log(self, message, exception=None):

        print(
            '********************************************************** ERROR '
            'LOG ***********************************************************\n'
        )
        print(message + str(exception))
        print(
            '*****************************************************************'
            '*****************************************************************'
        )
