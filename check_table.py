import os
import sys
sys.path.insert(0, r'D:\1-品创\langchain')

from feishu_reader import fetch_all_source_rows, get_tenant_access_token, list_source_records
from dotenv import load_dotenv
load_dotenv()

token = get_tenant_access_token()
app_token = os.getenv('FEISHU_SOURCE_APP_TOKEN')
table_id = os.getenv('FEISHU_SOURCE_TABLE_ID')

print('配置信息:')
print(f'  App Token: {app_token}')
print(f'  Table ID: {table_id}')
print()

records = list_source_records(token, app_token, table_id)
print(f'需求表总记录数: {len(records)}')
print()

if records:
    print('前3条记录详情:')
    for i, r in enumerate(records[:3], 1):
        fields = r.get('fields', {})
        title = fields.get('商品标题', 'N/A')
        status = fields.get('处理状态', 'N/A')
        review_count = fields.get('评价数量', 'N/A')
        image_count = fields.get('晒图数量', 'N/A')
        print(f'{i}. 商品标题: {title}')
        print(f'   处理状态: {status}')
        print(f'   评价数量: {review_count}')
        print(f'   晒图数量: {image_count}')
        print()
else:
    print('需求表为空，没有记录')
