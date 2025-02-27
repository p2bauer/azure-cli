# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

# pylint: disable=line-too-long
from argcomplete.completers import FilesCompleter

from azure.cli.core.commands.parameters import (
    get_resource_name_completion_list, name_type, get_enum_type, get_three_state_flag, tags_type)
from azure.cli.core.util import shell_safe_json_parse

from azure.cli.command_modules.cosmosdb._validators import (
    validate_ip_range_filter, validate_failover_policies, validate_capabilities,
    validate_virtual_network_rules)

from azure.cli.command_modules.cosmosdb.actions import (
    CreateLocation)


def load_arguments(self, _):

    from azure.mgmt.cosmosdb.models import KeyKind, DefaultConsistencyLevel, DatabaseAccountKind

    with self.argument_context('cosmosdb') as c:
        c.argument('account_name', arg_type=name_type, help='Name of the Cosmos DB database account', completer=get_resource_name_completion_list('Microsoft.DocumentDb/databaseAccounts'), id_part='name')
        c.argument('database_id', options_list=['--db-name', '-d'], help='Database Name')

    with self.argument_context('cosmosdb create') as c:
        c.argument('account_name', completer=None)

    for scope in ['cosmosdb create', 'cosmosdb update']:
        with self.argument_context(scope) as c:
            c.ignore('resource_group_location')
            c.argument('locations', nargs='+', action=CreateLocation)
            c.argument('tags', arg_type=tags_type)
            c.argument('default_consistency_level', arg_type=get_enum_type(DefaultConsistencyLevel), help="default consistency level of the Cosmos DB database account")
            c.argument('max_staleness_prefix', type=int, help="when used with Bounded Staleness consistency, this value represents the number of stale requests tolerated. Accepted range for this value is 1 - 2,147,483,647")
            c.argument('max_interval', type=int, help="when used with Bounded Staleness consistency, this value represents the time amount of staleness (in seconds) tolerated. Accepted range for this value is 1 - 100")
            c.argument('ip_range_filter', nargs='+', validator=validate_ip_range_filter, help="firewall support. Specifies the set of IP addresses or IP address ranges in CIDR form to be included as the allowed list of client IPs for a given database account. IP addresses/ranges must be comma-separated and must not contain any spaces")
            c.argument('kind', arg_type=get_enum_type(DatabaseAccountKind), help='The type of Cosmos DB database account to create')
            c.argument('enable_automatic_failover', arg_type=get_three_state_flag(), help='Enables automatic failover of the write region in the rare event that the region is unavailable due to an outage. Automatic failover will result in a new write region for the account and is chosen based on the failover priorities configured for the account.')
            c.argument('capabilities', nargs='+', validator=validate_capabilities, help='set custom capabilities on the Cosmos DB database account.')
            c.argument('enable_virtual_network', arg_type=get_three_state_flag(), help='Enables virtual network on the Cosmos DB database account')
            c.argument('virtual_network_rules', nargs='+', validator=validate_virtual_network_rules, help='ACL\'s for virtual network')
            c.argument('enable_multiple_write_locations', arg_type=get_three_state_flag(), help="Enable Multiple Write Locations")

    with self.argument_context('cosmosdb regenerate-key') as c:
        c.argument('key_kind', arg_type=get_enum_type(KeyKind))

    with self.argument_context('cosmosdb failover-priority-change') as c:
        c.argument('failover_policies', validator=validate_failover_policies, help="space-separated failover policies in 'regionName=failoverPriority' format. E.g eastus=0 westus=1", nargs='+')

    with self.argument_context('cosmosdb network-rule list') as c:
        c.argument('account_name', id_part=None)

    with self.argument_context('cosmosdb keys list') as c:
        c.argument('account_name', id_part=None)

    with self.argument_context('cosmosdb network-rule add') as c:
        c.argument('subnet', help="Name or ID of the subnet")
        c.argument('virtual_network', help="The name of the VNET, which must be provided in conjunction with the name of the subnet")
        c.argument("ignore_missing_vnet_service_endpoint", arg_type=get_three_state_flag(), help="Create firewall rule before the virtual network has vnet service endpoint enabled.")

    with self.argument_context('cosmosdb network-rule remove') as c:
        c.argument('subnet', help="Name or ID of the subnet")
        c.argument('virtual_network', help="The name of the VNET, which must be provided in conjunction with the name of the subnet")

    with self.argument_context('cosmosdb collection') as c:
        c.argument('collection_id', options_list=['--collection-name', '-c'], help='Collection Name')
        c.argument('throughput', type=int, help='Offer Throughput (RU/s)')
        c.argument('partition_key_path', help='Partition Key Path, e.g., \'/properties/name\'')
        c.argument('indexing_policy', type=shell_safe_json_parse, completer=FilesCompleter(), help='Indexing Policy, you can enter it as a string or as a file, e.g., --indexing-policy @policy-file.json)')
        c.argument('default_ttl', type=int, help='Default TTL. Provide 0 to disable.')

    with self.argument_context('cosmosdb database') as c:
        c.argument('throughput', type=int, help='Offer Throughput (RU/s)')
