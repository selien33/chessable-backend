# backend/azure_storage.py
from azure.data.tables import TableServiceClient, TableClient
from azure.core.exceptions import ResourceExistsError
import os

class AzureTableStorage:
    def __init__(self):
        connection_string = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
        self.table_service = TableServiceClient.from_connection_string(connection_string)
        self.ensure_tables_exist()
    
    def ensure_tables_exist(self):
        tables = ['users', 'games']
        for table_name in tables:
            try:
                self.table_service.create_table(table_name)
            except ResourceExistsError:
                pass
    
    def get_table_client(self, table_name):
        return self.table_service.get_table_client(table_name)
    
    def insert_entity(self, table_name, entity):
        table_client = self.get_table_client(table_name)
        table_client.create_entity(entity)
    
    def get_entity(self, table_name, partition_key, row_key):
        table_client = self.get_table_client(table_name)
        try:
            entity = table_client.get_entity(partition_key, row_key)
            return entity
        except:
            return None
    
    def update_entity(self, table_name, entity):
        table_client = self.get_table_client(table_name)
        table_client.update_entity(entity, mode='merge')
    
    def query_entities(self, table_name, filter_query):
        table_client = self.get_table_client(table_name)
        entities = table_client.query_entities(filter_query)
        return list(entities)
