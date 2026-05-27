import os
import sys
sys.path.insert(0, r'D:\1-品创\langchain')

from feishu_reader import get_tenant_access_token, list_source_records
from dotenv import load_dotenv
load_dotenv()

token = get_tenant_access_token()
app_token = os.getenv('FEISHU_SOURCE_APP_TOKEN')
table_id = os.getenv('FEISHU_SOURCE_TABLE_ID')

print('配置信息:')
print(f'  App Token: {app_token}')
print(f'  Table ID: {table_id}')
print()

try:
    records = list_source_records(token, app_token, table_id)
    print(f'需求表总记录数: {len(records)}')
    print()

    if records:
        print('所有记录处理状态:')
        for i, r in enumerate(records, 1):
            fields = r.get('fields', {})
            title = fields.get('商品标题', 'N/A')
            status = fields.get('处理状态', 'N/A')
            print(f'{i}. 商品: {title[:30]}... | 状态: {status}')
    else:
        print('需求表为空，没有记录')
except Exception as e:
    print(f'错误: {e}')
