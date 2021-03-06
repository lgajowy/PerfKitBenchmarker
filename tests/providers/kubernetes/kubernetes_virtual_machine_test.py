# Copyright 2017 PerfKitBenchmarker Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for providers.kubernetes.kubernetes_virtual_machine."""

# pylint: disable=not-context-manager

import json
import unittest
import contextlib2
import mock
from perfkitbenchmarker import virtual_machine
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.providers.kubernetes import kubernetes_virtual_machine
from tests import mock_flags

_COMPONENT = 'test_component'
_RUN_URI = 'fake_run_uri'
_NAME = 'fake_name'
_KUBECTL = 'fake_kubectl_path'
_KUBECONFIG = 'fake_kubeconfig_path'

_EXPECTED_CALL_BODY_WITHOUT_GPUS = """
{
    "spec": {
        "dnsPolicy":
            "ClusterFirst",
        "volumes": [],
        "containers": [{
            "name": "fake_name",
            "volumeMounts": [],
            "image": "test_image",
            "securityContext": {
                "privileged": null
            }
        }]
    },
    "kind": "Pod",
    "metadata": {
        "name": "fake_name",
        "labels": {
            "pkb": "fake_name"
        }
    },
    "apiVersion": "v1"
}
"""

_EXPECTED_CALL_BODY_WITH_2_GPUS = """
{
    "spec": {
        "dnsPolicy":
            "ClusterFirst",
        "volumes": [],
        "containers": [{
            "name": "fake_name",
            "volumeMounts": [],
            "image": "test_image",
            "securityContext": {
                "privileged": null
            },
            "resources" : {
              "limits": {
                "nvidia.com/gpu": "2"
                }
            }
        }]
    },
    "kind": "Pod",
    "metadata": {
        "name": "fake_name",
        "labels": {
            "pkb": "fake_name"
        }
    },
    "apiVersion": "v1"
}
"""

_EXPECTED_CALL_BODY_WITH_NVIDIA_CUDA_IMAGE = """
{
    "spec": {
        "dnsPolicy":
            "ClusterFirst",
        "volumes": [],
        "containers": [{
            "name": "fake_name",
            "volumeMounts": [],
            "image": "nvidia/cuda:8.0-devel-ubuntu16.04",
            "securityContext": {
                "privileged": null
            },
            "command": [
              "bash",
              "-c",
              "apt-get update && apt-get install -y sudo && sed -i '/env_reset/d' /etc/sudoers && sed -i '/secure_path/d' /etc/sudoers && sudo ldconfig && tail -f /dev/null"
            ]
        }]
    },
    "kind": "Pod",
    "metadata": {
        "name": "fake_name",
        "labels": {
            "pkb": "fake_name"
        }
    },
    "apiVersion": "v1"
}
"""


def get_write_mock_from_temp_file_mock(temp_file_mock):
  """Returns the write method mock from the NamedTemporaryFile mock.

  This can be used to make assertions about the calls make to write(),
  which exists on the instance returned from the NamedTemporaryFile mock.

  The reason for the __enter__() in this context is due to the fact
  that NamedTemporaryFile is used in a context manager inside
  kubernetes_helper.py.

  Args:
   temp_file_mock: mock object of the NamedTemporaryFile() contextManager
  """
  return temp_file_mock().__enter__().write


@contextlib2.contextmanager
def patch_critical_objects(stdout='', stderr='', return_code=0):
  with contextlib2.ExitStack() as stack:
    retval = (stdout, stderr, return_code)

    mflags = mock_flags.MockFlags()
    mflags.gcloud_path = 'gcloud'
    mflags.run_uri = _RUN_URI
    mflags.kubectl = _KUBECTL
    mflags.kubeconfig = _KUBECONFIG

    stack.enter_context(mock_flags.PatchFlags(mflags))
    stack.enter_context(mock.patch('__builtin__.open'))
    stack.enter_context(mock.patch(vm_util.__name__ + '.PrependTempDir'))

    # Save and return the temp_file mock here so that we can access the write()
    # call on the instance that the mock returned. This allows us to verify
    # that the body of the file is what we expect it to be (useful for
    # verifying that the pod.yml body was written correctly).
    temp_file = stack.enter_context(
        mock.patch(vm_util.__name__ + '.NamedTemporaryFile'))

    issue_command = stack.enter_context(
        mock.patch(vm_util.__name__ + '.IssueCommand', return_value=retval))

    yield issue_command, temp_file


class BaseKubernetesVirtualMachineTestCase(unittest.TestCase):

  def assertJsonEqual(self, str1, str2):
    json1 = json.loads(str1)
    json2 = json.loads(str2)
    self.assertEqual(
        json.dumps(json1, sort_keys=True),
        json.dumps(json2, sort_keys=True)
    )


class KubernetesVirtualMachineTestCase(
    BaseKubernetesVirtualMachineTestCase):

  @staticmethod
  def create_virtual_machine_spec():
    spec = virtual_machine.BaseVmSpec(
        _COMPONENT,
        image='test_image',
        install_packages=False,
        machine_type='test_machine_type',
        zone='test_zone')
    return spec

  def testCreate(self):
    spec = self.create_virtual_machine_spec()
    with patch_critical_objects() as (issue_command, _):
      kub_vm = kubernetes_virtual_machine.KubernetesVirtualMachine(spec)
      kub_vm._WaitForPodBootCompletion = lambda: None
      kub_vm._Create()
      command = issue_command.call_args[0][0]
      command_string = ' '.join(command[:4])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('{0} --kubeconfig={1} create -f'.format(
          _KUBECTL, _KUBECONFIG), command_string)

  def testCreatePodBodyWrittenCorrectly(self):
    spec = self.create_virtual_machine_spec()
    with patch_critical_objects() as (_, temp_file):
      kub_vm = kubernetes_virtual_machine.KubernetesVirtualMachine(spec)
      # Need to set the name explicitly on the instance because the test
      # running is currently using a single PKB instance, so the BaseVm
      # instance counter is at an unpredictable number at this stage, and it is
      # used to set the name.
      kub_vm.name = _NAME
      kub_vm._WaitForPodBootCompletion = lambda: None
      kub_vm._Create()

      write_mock = get_write_mock_from_temp_file_mock(temp_file)
      self.assertJsonEqual(
          write_mock.call_args[0][0],
          _EXPECTED_CALL_BODY_WITHOUT_GPUS
      )


class KubernetesVirtualMachineWithGpusTestCase(
    BaseKubernetesVirtualMachineTestCase):

  @staticmethod
  def create_virtual_machine_spec():
    spec = virtual_machine.BaseVmSpec(
        _COMPONENT,
        image='test_image',
        gpu_count=2,
        gpu_type='k80',
        install_packages=False,
        machine_type='test_machine_type',
        zone='test_zone')
    return spec

  def testCreate(self):
    spec = self.create_virtual_machine_spec()
    with patch_critical_objects() as (issue_command, _):
      kub_vm = kubernetes_virtual_machine.KubernetesVirtualMachine(spec)
      kub_vm._WaitForPodBootCompletion = lambda: None
      kub_vm._Create()
      command = issue_command.call_args[0][0]
      command_string = ' '.join(command[:4])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('{0} --kubeconfig={1} create -f'.format(
          _KUBECTL, _KUBECONFIG), command_string)

  def testCreatePodBodyWrittenCorrectly(self):
    spec = self.create_virtual_machine_spec()
    with patch_critical_objects() as (_, temp_file):
      kub_vm = kubernetes_virtual_machine.KubernetesVirtualMachine(spec)
      # Need to set the name explicitly on the instance because the test
      # running is currently using a single PKB instance, so the BaseVm
      # instance counter is at an unpredictable number at this stage, and it is
      # used to set the name.
      kub_vm.name = _NAME
      kub_vm._WaitForPodBootCompletion = lambda: None
      kub_vm._Create()

      write_mock = get_write_mock_from_temp_file_mock(temp_file)
      self.assertJsonEqual(
          write_mock.call_args[0][0],
          _EXPECTED_CALL_BODY_WITH_2_GPUS
      )


class KubernetesVirtualMachineWithNvidiaCudaImage(
    BaseKubernetesVirtualMachineTestCase):

  @staticmethod
  def create_virtual_machine_spec():
    spec = virtual_machine.BaseVmSpec(
        _COMPONENT,
        image='nvidia/cuda:8.0-devel-ubuntu16.04',
        install_packages=False,
        machine_type='test_machine_type',
        zone='test_zone')
    return spec

  def testCreatePodBodyWrittenCorrectly(self):
    spec = self.create_virtual_machine_spec()
    with patch_critical_objects() as (_, temp_file):
      kub_vm = kubernetes_virtual_machine.KubernetesVirtualMachine(spec)
      # Need to set the name explicitly on the instance because the test
      # running is currently using a single PKB instance, so the BaseVm
      # instance counter is at an unpredictable number at this stage, and it is
      # used to set the name.
      kub_vm.name = _NAME
      kub_vm._WaitForPodBootCompletion = lambda: None
      kub_vm._Create()

      write_mock = get_write_mock_from_temp_file_mock(temp_file)
      self.assertJsonEqual(
          write_mock.call_args[0][0],
          _EXPECTED_CALL_BODY_WITH_NVIDIA_CUDA_IMAGE
      )
