# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import os
import re
import unittest
from datetime import datetime, timedelta
from azure.cli.testsdk import (ScenarioTest, ResourceGroupPreparer, StorageAccountPreparer,
                               JMESPathCheck, JMESPathCheckExists, NoneCheck, api_version_constraint)
from knack.util import CLIError
from azure.cli.core.profiles import ResourceType

from azure.cli.command_modules.storage._client_factory import MISSING_CREDENTIALS_ERROR_MESSAGE
from ..storage_test_util import StorageScenarioMixin


@api_version_constraint(ResourceType.MGMT_STORAGE, min_api='2016-12-01')
class StorageBlobUploadTests(StorageScenarioMixin, ScenarioTest):
    @ResourceGroupPreparer()
    @StorageAccountPreparer(parameter_name='source_account')
    @StorageAccountPreparer(parameter_name='target_account')
    def test_storage_blob_incremental_copy(self, resource_group, source_account, target_account):
        source_file = self.create_temp_file(16)
        source_account_info = self.get_account_info(resource_group, source_account)
        source_container = self.create_container(source_account_info)
        self.storage_cmd('storage blob upload -c {} -n src -f "{}" -t page', source_account_info,
                         source_container, source_file)

        snapshot = self.storage_cmd('storage blob snapshot -c {} -n src', source_account_info,
                                    source_container).get_output_in_json()['snapshot']

        target_account_info = self.get_account_info(resource_group, target_account)
        target_container = self.create_container(target_account_info)
        self.storage_cmd('storage blob incremental-copy start --source-container {} --source-blob '
                         'src --source-account-name {} --source-account-key {} --source-snapshot '
                         '{} --destination-container {} --destination-blob backup',
                         target_account_info, source_container, source_account,
                         source_account_info[1], snapshot, target_container)

    def test_storage_blob_no_credentials_scenario(self):
        source_file = self.create_temp_file(1)
        self.cmd('storage blob upload -c foo -n bar -f "' + source_file + '"', expect_failure=CLIError)

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_upload_small_file(self, resource_group, storage_account):
        for blob_type in ['block', 'page']:
            self.verify_blob_upload_and_download(resource_group, storage_account, 1, blob_type, 0)

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_upload_midsize_file(self, resource_group, storage_account):
        for blob_type in ['block', 'page']:
            self.verify_blob_upload_and_download(resource_group, storage_account, 4096, 'block', 0)

    def verify_blob_upload_and_download(self, group, account, file_size_kb, blob_type,
                                        block_count=0, skip_download=False):
        local_dir = self.create_temp_dir()
        local_file = self.create_temp_file(file_size_kb)
        blob_name = self.create_random_name(prefix='blob', length=24)
        account_info = self.get_account_info(group, account)

        container = self.create_container(account_info)

        self.storage_cmd('storage blob exists -n {} -c {}', account_info, blob_name, container) \
            .assert_with_checks(JMESPathCheck('exists', False))

        self.storage_cmd('storage blob upload -c {} -f "{}" -n {} --type {}', account_info,
                         container, local_file, blob_name, blob_type)
        self.storage_cmd('storage blob exists -n {} -c {}', account_info, blob_name, container) \
            .assert_with_checks(JMESPathCheck('exists', True))
        self.storage_cmd('storage blob list -c {} -otable --num-results 1', account_info, container)

        show_result = self.storage_cmd('storage blob show -n {} -c {}', account_info, blob_name,
                                       container).get_output_in_json()
        self.assertEqual(show_result.get('name'), blob_name)
        if blob_type == 'page':
            self.assertEqual(type(show_result.get('properties').get('pageRanges')), list)
        else:
            self.assertEqual(show_result.get('properties').get('pageRanges'), None)

        expiry = (datetime.utcnow() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%MZ')
        sas = self.storage_cmd('storage blob generate-sas -n {} -c {} --expiry {} --permissions '
                               'r --https-only', account_info, blob_name, container, expiry).output
        self.assertTrue(sas)
        self.assertIn('&sig=', sas)

        self.storage_cmd('storage blob update -n {} -c {} --content-type application/test-content',
                         account_info, blob_name, container)

        self.storage_cmd('storage blob show -n {} -c {}', account_info, blob_name, container) \
            .assert_with_checks(
            [JMESPathCheck('properties.contentSettings.contentType', 'application/test-content'),
             JMESPathCheck('properties.contentLength', file_size_kb * 1024)])

        # check that blob properties can be set back to null
        self.storage_cmd('storage blob update -n {} -c {} --content-type ""',
                         account_info, blob_name, container)

        self.storage_cmd('storage blob show -n {} -c {}', account_info, blob_name, container) \
            .assert_with_checks(JMESPathCheck('properties.contentSettings.contentType', None))

        self.storage_cmd('storage blob service-properties show', account_info) \
            .assert_with_checks(JMESPathCheck('hourMetrics.enabled', True))

        if not skip_download:
            downloaded = os.path.join(local_dir, 'test.file')

            self.storage_cmd('storage blob download -n {} -c {} --file "{}"',
                             account_info, blob_name, container, downloaded)
            self.assertTrue(os.path.isfile(downloaded), 'The file is not downloaded.')
            self.assertEqual(file_size_kb * 1024, os.stat(downloaded).st_size,
                             'The download file size is not right.')
            self.storage_cmd('storage blob download -n {} -c {} --file "{}" --start-range 10 --end-range 499',
                             account_info, blob_name, container, downloaded)
            self.assertEqual(490, os.stat(downloaded).st_size,
                             'The download file size is not right.')

        # Verify the requests in cassette to ensure the count of the block requests is expected
        # This portion of validation doesn't verify anything during playback because the recording
        # is fixed.

        def is_block_put_req(request):
            if request.method != 'PUT':
                return False

            if not re.search('/cont[0-9]+/blob[0-9]+', request.path):
                return False

            comp_block = False
            has_blockid = False
            for key, value in request.query:
                if key == 'comp' and value == 'block':
                    comp_block = True
                elif key == 'blockid':
                    has_blockid = True

            return comp_block and has_blockid

        requests = self.cassette.requests
        put_blocks = [request for request in requests if is_block_put_req(request)]
        self.assertEqual(block_count, len(put_blocks),
                         'The expected number of block put requests is {} but the actual '
                         'number is {}.'.format(block_count, len(put_blocks)))

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_socket_timeout(self, resource_group, storage_account):
        local_dir = self.create_temp_dir()
        local_file = self.create_temp_file(1)
        blob_name = self.create_random_name(prefix='blob', length=24)
        account_info = self.get_account_info(resource_group, storage_account)

        container = self.create_container(account_info)

        from azure.common import AzureException
        with self.assertRaises(AzureException):
            self.storage_cmd('storage blob upload -c {} -f "{}" -n {} --type block --socket-timeout -11',
                             account_info, container, local_file, blob_name)

        self.storage_cmd('storage blob exists -n {} -c {}', account_info, blob_name, container) \
            .assert_with_checks(JMESPathCheck('exists', False))

        self.storage_cmd('storage blob upload -c {} -f "{}" -n {} --type block --socket-timeout 10',
                         account_info, container, local_file, blob_name)
        self.storage_cmd('storage blob exists -n {} -c {}', account_info, blob_name, container) \
            .assert_with_checks(JMESPathCheck('exists', True))

        self.storage_cmd('storage blob show -n {} -c {}', account_info, blob_name, container) \
            .assert_with_checks(JMESPathCheck('name', blob_name))

        downloaded = os.path.join(local_dir, 'test.file')

        self.storage_cmd('storage blob download -n {} -c {} --file "{}" --socket-timeout 10',
                         account_info, blob_name, container, downloaded)
        self.assertTrue(os.path.isfile(downloaded), 'The file is not downloaded.')

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_lease_operations(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        local_file = self.create_temp_file(128)
        c = self.create_container(account_info)
        b = self.create_random_name('blob', 24)
        proposed_lease_id = 'abcdabcd-abcd-abcd-abcd-abcdabcdabcd'
        new_lease_id = 'dcbadcba-dcba-dcba-dcba-dcbadcbadcba'
        date = '2016-04-01t12:00z'

        self.storage_cmd('storage blob upload -c {} -n {} -f "{}"', account_info, c, b, local_file)

        # test lease operations
        self.storage_cmd('storage blob lease acquire --lease-duration 60 -b {} -c {} '
                         '--if-modified-since {} --proposed-lease-id {}', account_info, b, c, date,
                         proposed_lease_id)
        self.storage_cmd('storage blob show -n {} -c {}', account_info, b, c) \
            .assert_with_checks(JMESPathCheck('properties.lease.duration', 'fixed'),
                                JMESPathCheck('properties.lease.state', 'leased'),
                                JMESPathCheck('properties.lease.status', 'locked'))
        self.storage_cmd('storage blob lease change -b {} -c {} --lease-id {} '
                         '--proposed-lease-id {}', account_info, b, c, proposed_lease_id,
                         new_lease_id)
        self.storage_cmd('storage blob lease renew -b {} -c {} --lease-id {}', account_info, b, c,
                         new_lease_id)
        self.storage_cmd('storage blob show -n {} -c {}', account_info, b, c) \
            .assert_with_checks(JMESPathCheck('properties.lease.duration', 'fixed'),
                                JMESPathCheck('properties.lease.state', 'leased'),
                                JMESPathCheck('properties.lease.status', 'locked'))
        self.storage_cmd('storage blob lease break -b {} -c {} --lease-break-period 30',
                         account_info, b, c)
        self.storage_cmd('storage blob show -n {} -c {}', account_info, b, c) \
            .assert_with_checks(JMESPathCheck('properties.lease.duration', None),
                                JMESPathCheck('properties.lease.state', 'breaking'),
                                JMESPathCheck('properties.lease.status', 'locked'))
        self.storage_cmd('storage blob lease release -b {} -c {} --lease-id {}', account_info, b, c,
                         new_lease_id)
        self.storage_cmd('storage blob show -n {} -c {}', account_info, b, c) \
            .assert_with_checks(JMESPathCheck('properties.lease.duration', None),
                                JMESPathCheck('properties.lease.state', 'available'),
                                JMESPathCheck('properties.lease.status', 'unlocked'))

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_snapshot_operations(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        local_file = self.create_temp_file(128)
        c = self.create_container(account_info)
        b = self.create_random_name('blob', 24)

        self.storage_cmd('storage blob upload -c {} -n {} -f "{}"', account_info, c, b, local_file)

        snapshot_dt = self.storage_cmd('storage blob snapshot -c {} -n {}', account_info, c, b) \
            .get_output_in_json()['snapshot']
        self.storage_cmd('storage blob exists -n {} -c {} --snapshot {}', account_info, b, c,
                         snapshot_dt) \
            .assert_with_checks(JMESPathCheck('exists', True))

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_metadata_operations(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        c = self.create_container(account_info)
        b = self.create_random_name('blob', 24)

        self.storage_cmd('storage blob upload -c {} -n {} -f "{}"', account_info, c, b, __file__)
        self.storage_cmd('storage blob metadata update -n {} -c {} --metadata a=b c=d',
                         account_info, b, c)
        self.storage_cmd('storage blob metadata show -n {} -c {}', account_info, b, c) \
            .assert_with_checks(JMESPathCheck('a', 'b'), JMESPathCheck('c', 'd'))
        self.storage_cmd('storage blob metadata update -n {} -c {}', account_info, b, c)
        self.storage_cmd('storage blob metadata show -n {} -c {}', account_info, b, c) \
            .assert_with_checks(NoneCheck())

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_container_operations(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        c = self.create_container(account_info)
        proposed_lease_id = 'abcdabcd-abcd-abcd-abcd-abcdabcdabcd'
        new_lease_id = 'dcbadcba-dcba-dcba-dcba-dcbadcbadcba'
        date = '2016-04-01t12:00z'

        self.storage_cmd('storage container exists -n {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('exists', True))

        self.storage_cmd('storage container set-permission -n {} --public-access blob',
                         account_info, c)
        self.storage_cmd('storage container show-permission -n {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('publicAccess', 'blob'))
        self.storage_cmd('storage container set-permission -n {} --public-access off', account_info,
                         c)
        self.storage_cmd('storage container show-permission -n {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('publicAccess', 'off'))

        self.storage_cmd('storage container show -n {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('name', c))

        self.assertIn(c, self.storage_cmd('storage container list --query "[].name"',
                                          account_info).get_output_in_json())

        self.storage_cmd('storage container metadata update -n {} --metadata foo=bar moo=bak',
                         account_info, c)
        self.storage_cmd('storage container metadata show -n {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('foo', 'bar'), JMESPathCheck('moo', 'bak'))
        self.storage_cmd('storage container metadata update -n {}', account_info, c)
        self.storage_cmd('storage container metadata show -n {}', account_info, c) \
            .assert_with_checks(NoneCheck())

        # test lease operations
        self.storage_cmd('storage container lease acquire --lease-duration 60 -c {} '
                         '--if-modified-since {} --proposed-lease-id {}', account_info, c, date,
                         proposed_lease_id)
        self.storage_cmd('storage container show --name {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('properties.lease.duration', 'fixed'),
                                JMESPathCheck('properties.lease.state', 'leased'),
                                JMESPathCheck('properties.lease.status', 'locked'))
        self.storage_cmd('storage container lease change -c {} --lease-id {} '
                         '--proposed-lease-id {}', account_info, c, proposed_lease_id, new_lease_id)
        self.storage_cmd('storage container lease renew -c {} --lease-id {}',
                         account_info, c, new_lease_id)
        self.storage_cmd('storage container show -n {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('properties.lease.duration', 'fixed'),
                                JMESPathCheck('properties.lease.state', 'leased'),
                                JMESPathCheck('properties.lease.status', 'locked'))
        self.storage_cmd('storage container lease break -c {} --lease-break-period 30',
                         account_info, c)
        self.storage_cmd('storage container show --name {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('properties.lease.duration', None),
                                JMESPathCheck('properties.lease.state', 'breaking'),
                                JMESPathCheck('properties.lease.status', 'locked'))
        self.storage_cmd('storage container lease release -c {} --lease-id {}', account_info, c,
                         new_lease_id)
        self.storage_cmd('storage container show --name {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('properties.lease.duration', None),
                                JMESPathCheck('properties.lease.state', 'available'),
                                JMESPathCheck('properties.lease.status', 'unlocked'))

        self.assertIn('sig=', self.storage_cmd('storage container generate-sas -n {}', account_info,
                                               c).output)

        # verify delete operation
        self.storage_cmd('storage container delete --name {} --fail-not-exist', account_info, c) \
            .assert_with_checks(JMESPathCheck('deleted', True))
        self.storage_cmd('storage container exists -n {}', account_info, c) \
            .assert_with_checks(JMESPathCheck('exists', False))

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_soft_delete(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        container = self.create_container(account_info)

        # create a blob
        local_file = self.create_temp_file(1)
        blob_name = self.create_random_name(prefix='blob', length=24)

        self.storage_cmd('storage blob upload -c {} -f "{}" -n {} --type block', account_info,
                         container, local_file, blob_name)
        self.assertEqual(len(self.storage_cmd('storage blob list -c {}',
                                              account_info, container).get_output_in_json()), 1)

        # set delete-policy to enable soft-delete
        self.storage_cmd('storage blob service-properties delete-policy update --enable true --days-retained 2',
                         account_info)
        self.storage_cmd('storage blob service-properties delete-policy show',
                         account_info).assert_with_checks(JMESPathCheck('enabled', True),
                                                          JMESPathCheck('days', 2))

        # soft-delete and check
        self.storage_cmd('storage blob delete -c {} -n {}', account_info, container, blob_name)
        self.assertEqual(len(self.storage_cmd('storage blob list -c {}',
                                              account_info, container).get_output_in_json()), 0)

        self.assertEqual(len(self.storage_cmd('storage blob list -c {} --include d',
                                              account_info, container).get_output_in_json()), 1)

        # undelete and check
        self.storage_cmd('storage blob undelete -c {} -n {}', account_info, container, blob_name)
        self.assertEqual(len(self.storage_cmd('storage blob list -c {}',
                                              account_info, container).get_output_in_json()), 1)

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_append(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        container = self.create_container(account_info)

        # create an append blob
        local_file = self.create_temp_file(1)
        blob_name = self.create_random_name(prefix='blob', length=24)

        self.storage_cmd('storage blob upload -c {} -f "{}" -n {} --type append --if-none-match *', account_info,
                         container, local_file, blob_name)
        self.assertEqual(len(self.storage_cmd('storage blob list -c {}',
                                              account_info, container).get_output_in_json()), 1)

        # append if-none-match should throw exception
        with self.assertRaises(Exception):
            self.storage_cmd('storage blob upload -c {} -f "{}" -n {} --type append --if-none-match *', account_info,
                             container, local_file, blob_name)

    @ResourceGroupPreparer()
    def test_storage_blob_update_service_properties(self, resource_group):
        storage_account = self.create_random_name(prefix='account', length=24)

        self.cmd('storage account create -n {} -g {} --kind StorageV2'.format(storage_account, resource_group))
        account_info = self.get_account_info(resource_group, storage_account)

        self.storage_cmd('storage blob service-properties show', account_info) \
            .assert_with_checks(JMESPathCheck('staticWebsite.enabled', False))

        self.storage_cmd('storage blob service-properties update --static-website --index-document index.html '
                         '--404-document error.html', account_info)

        self.storage_cmd('storage blob service-properties update --delete-retention --delete-retention-period 1',
                         account_info)

        self.storage_cmd('storage blob service-properties show', account_info) \
            .assert_with_checks(JMESPathCheck('staticWebsite.enabled', True),
                                JMESPathCheck('staticWebsite.errorDocument_404Path', 'error.html'),
                                JMESPathCheck('staticWebsite.indexDocument', 'index.html'),
                                JMESPathCheck('deleteRetentionPolicy.enabled', True),
                                JMESPathCheck('deleteRetentionPolicy.days', 1))

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_copy_cancel_nopendingcopyoperation_error(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        c = self.create_container(account_info)
        b = self.create_random_name('blob', 24)
        local_file = self.create_temp_file(1)
        copy_id = 'abcdabcd-abcd-abcd-abcd-abcdabcdabcd'

        self.storage_cmd('storage blob upload -c {} -n {} -f "{}"', account_info, c, b, local_file)
        with self.assertRaisesRegexp(TypeError, "'CommandResultItem' object is not iterable"):
            self.storage_cmd('storage blob copy cancel -c {} -b {} --copy-id {}', account_info, c, b, copy_id)

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_generate_sas_full_uri(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        c = self.create_container(account_info)
        b = self.create_random_name('blob', 24)

        expiry = (datetime.utcnow() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%MZ')
        blob_uri = self.storage_cmd('storage blob generate-sas -n {} -c {} --expiry {} --permissions '
                                    'r --https-only --full-uri', account_info, b, c, expiry).output
        self.assertTrue(blob_uri)
        self.assertIn('&sig=', blob_uri)
        self.assertTrue(blob_uri.startswith('"https://clitest000002.blob.core.windows.net/cont000003/blob000004?s'))

    @ResourceGroupPreparer()
    @StorageAccountPreparer()
    def test_storage_blob_generate_sas_as_user(self, resource_group, storage_account):
        account_info = self.get_account_info(resource_group, storage_account)
        c = self.create_container(account_info)
        b = self.create_random_name('blob', 24)

        expiry = (datetime.utcnow() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%MZ')
        blob_sas = self.cmd('storage blob generate-sas --account-name {} -n {} -c {} --expiry {} --permissions '
                            'r --https-only --as-user --auth-mode login'.format(storage_account, b, c, expiry)).output
        self.assertIn('&sig=', blob_sas)
        self.assertIn('skoid=', blob_sas)
        self.assertIn('sktid=', blob_sas)
        self.assertIn('skt=', blob_sas)
        self.assertIn('ske=', blob_sas)
        self.assertIn('sks=', blob_sas)
        self.assertIn('skv=', blob_sas)

        container_sas = self.cmd('storage container generate-sas --account-name {} -n {} --expiry {} --permissions '
                                 'r --https-only --as-user --auth-mode login'.format(storage_account, c, expiry)).output
        self.assertIn('&sig=', container_sas)
        self.assertIn('skoid=', container_sas)
        self.assertIn('sktid=', container_sas)
        self.assertIn('skt=', container_sas)
        self.assertIn('ske=', container_sas)
        self.assertIn('sks=', container_sas)
        self.assertIn('skv=', container_sas)
        self.assertIn('skv=', container_sas)


if __name__ == '__main__':
    unittest.main()
