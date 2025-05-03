# backend/azure_storage.py
from azure.data.tables import TableServiceClient, TableClient
from azure.core.exceptions import ResourceExistsError, ServiceRequestError
import os

class AzureTableStorage:
    def __init__(self):
        connection_string = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
        if not connection_string:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING environment variable is not set")
        
        try:
            self.table_service = TableServiceClient.from_connection_string(connection_string)
            self.ensure_tables_exist()
        except ServiceRequestError as e:
            print(f"Failed to connect to Azure Storage: {e}")
            raise e
    
    def ensure_tables_exist(self):
        tables = ['users', 'games', 'waitinglist', 'currentgames']
        for table_name in tables:
            try:
                self.table_service.create_table(table_name)
                print(f"Created table: {table_name}")
            except ResourceExistsError:
                print(f"Table {table_name} already exists")
            except Exception as e:
                print(f"Error creating table {table_name}: {str(e)}")
    
    def get_table_client(self, table_name):
        return self.table_service.get_table_client(table_name)
    
    def insert_entity(self, table_name, entity):
        table_client = self.get_table_client(table_name)
        return table_client.create_entity(entity)
    
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
    
    def delete_entity(self, table_name, partition_key, row_key):
        table_client = self.get_table_client(table_name)
        try:
            table_client.delete_entity(partition_key, row_key)
            return True
        except:
            return False
    
    def query_entities(self, table_name, filter_query):
        table_client = self.get_table_client(table_name)
        entities = table_client.query_entities(filter_query)
        return list(entities)